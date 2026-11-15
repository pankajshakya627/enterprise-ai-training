"""Render policy chat, answer evaluation and feedback review through HTTP clients."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx
import streamlit as st
from pydantic import ValidationError

from app.core.config import get_ui_settings
from app.models.answer import AnswerResponse
from app.models.evaluation import EvaluatedAnswerResponse
from app.models.retrieval import RetrieveRequest, RetrieveResponse
from app.ui.agent import agent_workspace, chat_workspace, clear_conversation


@st.cache_resource
def api_client(base_url: str) -> httpx.Client:
    """Return the shared cached HTTP client for the selected API base URL."""
    return httpx.Client(base_url=base_url, timeout=180)


def show_answer(result: AnswerResponse) -> None:
    """Render answer status and expand the exact cited policy evidence."""
    if result.status != "answered":
        st.warning(result.answer)
    else:
        st.write(result.answer)
    for item in result.citations:
        source = item.matched_child
        with st.expander(f"[{item.evidence_id}] {source.title} · v{source.policy_version}"):
            st.text(item.quote)
            st.caption(
                f"{source.doc_id} · {f'matched clause {source.clause}' if source.clause else 'complete instrument'}"
            )


def feedback_form(client: httpx.Client, turn: dict[str, Any]) -> None:
    """Submit feedback for the retained answer without replacing prior saved feedback."""
    if turn.get("feedback_saved"):
        st.caption("Feedback saved for review.")
        return
    with st.form(f"answer_feedback_{turn['id']}"):
        helpful = st.radio("Was this answer helpful?", ["Yes", "No"], horizontal=True)
        correction = st.text_area("Correction or missing evidence", max_chars=4000)
        submitted = st.form_submit_button("Send feedback")
    if submitted:
        try:
            response = client.post(
                "/feedback",
                json={
                    "request": turn["request"],
                    "response": turn["response"],
                    "helpful": helpful == "Yes",
                    "submitter_id": st.session_state["feedback_submitter_id"],
                    "submission_id": turn["id"],
                    "correction": correction,
                },
            )
            response.raise_for_status()
            turn["feedback_saved"] = True
            st.success("Feedback saved for review.")
        except httpx.HTTPError:
            st.error("Feedback could not be saved. Please try again.")


def review_feedback(client: httpx.Client) -> None:
    """Load feedback records and submit reviewer-authorized review updates."""
    token = st.text_input(
        "Reviewer token",
        type="password",
        key="reviewer_token",
        on_change=lambda: st.session_state.pop("feedback_records", None),
    )
    if not token:
        st.info("Enter your reviewer token to load feedback.")
        return
    headers = {"Authorization": f"Bearer {token}"}
    if st.button("Load feedback for review"):
        st.session_state.pop("feedback_records", None)
        try:
            response = client.get("/feedback", headers=headers)
            response.raise_for_status()
            st.session_state["feedback_records"] = response.json()
        except httpx.HTTPError:
            st.error("Feedback could not be loaded.")
    records = st.session_state.get("feedback_records", [])
    for record in records:
        identifier = record["feedback_id"]
        with st.expander(f"{record['status']} · {record['request']['query']}"):
            st.write(record["response"]["answer"])
            st.text(record["correction"] or "No correction supplied.")
            if record["status"] != "pending":
                st.caption(f"Reviewed by {record['reviewer']}: {record['review_note']}")
                continue
            with st.form(f"review_{identifier}"):
                reviewer = st.text_input("Reviewer name")
                decision = st.selectbox("Decision", ["accepted", "rejected"])
                note = st.text_area("Review note", max_chars=4000)
                submitted = st.form_submit_button("Save review")
            if submitted:
                if not reviewer.strip() or not note.strip():
                    st.error("Enter a reviewer name and review note.")
                    continue
                try:
                    response = client.post(
                        f"/feedback/{identifier}/review",
                        headers=headers,
                        json={
                            "status": decision,
                            "reviewer": reviewer,
                            "review_note": note,
                        },
                    )
                    response.raise_for_status()
                    record.update(status=decision, reviewer=reviewer, review_note=note)
                    st.rerun()
                except httpx.HTTPError:
                    st.error("Review could not be saved. Reload the feedback list and try again.")


def open_chat(identifier: str) -> None:
    """Select the local conversation and return to the policy-chat workspace."""
    st.session_state["active_chat"] = identifier
    st.session_state["workspace"] = "Policy chat"


def new_chat() -> None:
    """Reset the active chat without carrying pending customer details to a new query."""
    if st.session_state["workspace"] == "Agent chat":
        clear_conversation()
    else:
        open_chat(uuid4().hex)


def send_question(client: httpx.Client, turn: dict[str, Any]) -> None:
    """Call the existing API; chat history remains local to this UI session."""
    turn.pop("error", None)
    try:
        evaluating = turn["mode"] == "Answer" and turn.get("evaluate_quality", False)
        with st.spinner(
            "Checking policy sources and scoring the answer…"
            if evaluating
            else "Checking policy sources…"
        ):
            payload = turn["request"]
            if evaluating:
                payload = {**payload, "reference_answer": turn.get("reference_answer")}
            response = client.post(
                "/answer/evaluated"
                if evaluating
                else "/answer"
                if turn["mode"] == "Answer"
                else "/retrieve",
                json=payload,
                timeout=300 if evaluating else 180,
            )
            response.raise_for_status()
            if evaluating:
                result = EvaluatedAnswerResponse.model_validate(response.json())
                turn["response"] = result.answer.model_dump(mode="json")
                turn["quality"] = result.quality.model_dump(mode="json")
                return
            schema = AnswerResponse if turn["mode"] == "Answer" else RetrieveResponse
            turn["response"] = schema.model_validate(response.json()).model_dump(mode="json")
    except httpx.HTTPError:
        turn["error"] = "The policy service is unavailable. Please try again."
    except ValueError:
        turn["error"] = "The policy service returned an invalid response. Please try again."


def show_turn(client: httpx.Client, turn: dict[str, Any]) -> bool:
    """Render one question and its evidence, returning whether to retry it.

    Returns:
        True when the user requests a retry for this retained turn.
    """
    request = turn["request"]
    with st.chat_message("user", avatar=":material/person:"):
        st.markdown(request["query"])
        st.caption(
            f"Policy date: {request['as_of']} · {request['product_id'] or 'All products'}"
            f" · {turn['mode']} · Up to {request['top_k']} sources + linked amendments"
        )
    with st.chat_message("assistant", avatar=":material/account_balance:"):
        if turn.get("error"):
            st.error(turn["error"])
            return st.button("Try again", key=f"retry_{turn['id']}", icon=":material/refresh:")
        if turn["mode"] == "Answer":
            result = AnswerResponse.model_validate(turn["response"])
            show_answer(result)
            if result.citations:
                st.caption(f"{len(result.citations)} cited sources · Policy date: {result.as_of}")
            datadog_url = (turn.get("quality") or {}).get("datadog_url")
            if datadog_url:
                st.link_button("View evaluation in Datadog", datadog_url)
            with st.expander("Give feedback", icon=":material/rate_review:"):
                feedback_form(client, turn)
        else:
            evidence = RetrieveResponse.model_validate(turn["response"])
            if not evidence.results:
                st.info("No evidence matched this question and policy date.")
            else:
                st.markdown(f"Found **{len(evidence.results)} policy sources** to explore.")
            for hit in evidence.results:
                with st.expander(f"{hit.rank}. {hit.citation.title} · {hit.citation.doc_id}"):
                    st.text(hit.parent_text)
                    st.caption(
                        f"Version {hit.citation.policy_version} · "
                        f"Clause {hit.citation.clause or 'front matter'} · "
                        f"Effective {hit.citation.effective_date}"
                    )
            for amendment in evidence.amendments:
                with st.expander(f"Linked amendment: {amendment.citation.title}"):
                    st.text(amendment.parent_text)
                    st.caption(
                        f"{amendment.citation.doc_id} · Complete instrument, including applicability"
                    )
    return False


def main() -> None:
    # shortcut: restored open sidebars can shift at bootstrap; revisit after Streamlit layout fixes.
    """Render the configured Streamlit policy and service-request workspaces."""
    st.set_page_config(
        page_title="NexaAssist",
        page_icon=":material/account_balance:",
        layout="centered",
        initial_sidebar_state="collapsed",
    )
    # Native date segments and dimmed captions failed the browser accessibility audit.
    st.html("""<style>
    [data-testid="stCaptionContainer"] { opacity: 0.85; }
    [data-testid="stDateInput"] [role="spinbutton"] {
        min-width: 24px; min-height: 24px; line-height: 24px;
        display: inline-block; text-align: center;
    }
    </style>""")
    settings = get_ui_settings()
    client = api_client(settings.api_base_url)
    st.session_state.setdefault("conversations", {})
    st.session_state.setdefault("feedback_submitter_id", str(uuid4()))
    st.session_state.setdefault("active_chat", uuid4().hex)
    st.session_state.setdefault("workspace", "Agent chat")
    if st.session_state.pop("clear_quality_reference", False):
        st.session_state["quality_reference"] = ""
    conversations = st.session_state["conversations"]

    with st.sidebar:
        st.subheader(":material/account_balance: NexaAssist", anchor=False)
        st.caption("NexaBank policy workspace")
        st.button(
            "New chat",
            icon=":material/edit_square:",
            width="stretch",
            on_click=new_chat,
            key="new_chat",
        )
        st.radio(
            "Workspace",
            ["Agent chat", "Policy chat", "Review feedback", "Service requests"],
            key="workspace",
            label_visibility="collapsed",
        )
        if conversations:
            st.caption("Recent chats")
            for identifier, conversation in reversed(list(conversations.items())):
                st.button(
                    conversation["title"],
                    key=f"chat_{identifier}",
                    icon=":material/chat_bubble_outline:",
                    width="stretch",
                    type="secondary"
                    if identifier == st.session_state["active_chat"]
                    else "tertiary",
                    on_click=open_chat,
                    args=(identifier,),
                )
        with st.expander(
            "Policy scope",
            expanded=st.session_state["workspace"] != "Agent chat",
            icon=":material/tune:",
        ):
            as_of = st.date_input(
                "Policy date",
                value=datetime.now(ZoneInfo(settings.ui_timezone)).date(),
                key="policy_date",
                help="Find evidence valid on this date.",
            )
            product_id = st.text_input(
                "Product ID (optional)",
                key="product_id",
                autocomplete="off",
                placeholder="All products",
                help="Examples: NEXA-HL, NEXA-PL, NEXA-LAP, NEXA-AL.",
            )
            top_k = st.slider("Number of sources", 1, 20, 5, key="source_count")
            mode = st.segmented_control(
                "Response",
                ["Answer", "Evidence"],
                default="Answer",
                required=True,
                key="response_mode",
                width="stretch",
            )
        if st.session_state["workspace"] != "Agent chat":
            with st.expander("Answer quality", expanded=True, icon=":material/analytics:"):
                evaluate_quality = st.toggle(
                    "Evaluate with RAGAS",
                    value=True,
                    key="evaluate_quality",
                    disabled=mode != "Answer",
                )
                st.caption("Uses additional model calls to score each new answer.")
                reference_answer = st.text_area(
                    "Reference answer for the next question (optional)",
                    key="quality_reference",
                    max_chars=8000,
                    placeholder="Expected answer for this question",
                    disabled=mode != "Answer" or not evaluate_quality,
                    help="Context precision and recall require an expected answer. Exact golden-case matches use the saved reference automatically.",
                )
        st.caption("Synthetic NexaBank policies for training.")
        st.caption("Chats stay in this browser session and reset when it ends.")

    if st.session_state["workspace"] == "Agent chat":
        chat_workspace(api_client(settings.agent_api_base_url), as_of, product_id.strip() or None)
        return

    if st.session_state["workspace"] == "Service requests":
        agent_workspace(api_client(settings.agent_api_base_url), as_of, product_id.strip() or None)
        return

    if st.session_state["workspace"] == "Review feedback":
        st.title("Review feedback", anchor=False)
        st.caption("Check suggested corrections against the source evidence.")
        review_feedback(client)
        return

    with st.container(horizontal=True, horizontal_alignment="distribute"):
        st.markdown("**NexaAssist** / Policy chat")
        st.badge("Synthetic training data", icon=":material/science:", color="blue")

    identifier = st.session_state["active_chat"]
    conversation = conversations.get(identifier)
    turns = conversation["turns"] if conversation else []
    welcome = st.empty()
    suggestion: str | None = None
    if not turns:
        with welcome.container():
            st.space("large")
            st.title("NexaBank policy questions", anchor=False)
            st.markdown("Check NexaBank policy terms and the sources behind each answer.")
            st.caption("Ask about eligibility, loan limits, fees, or policy changes.")
            st.space("small")
            suggestions = [
                (
                    "Home loan limits",
                    "What is the maximum loan amount for NexaHome Loan?",
                    ":material/home:",
                ),
                (
                    "Personal loan eligibility",
                    "What are the eligibility criteria for NexaFlex Personal Loan?",
                    ":material/person_check:",
                ),
                (
                    "Auto loan fees",
                    "What are the processing fees for NexaDrive Auto Loan?",
                    ":material/directions_car:",
                ),
            ]
            with st.container(horizontal=True):
                for label, question, icon in suggestions:
                    if st.button(label, icon=icon, help=question):
                        suggestion = question
            st.caption("Suggestions use the policy scope selected in the sidebar.")
    retry: dict[str, Any] | None = None
    for turn in turns:
        if show_turn(client, turn):
            retry = turn
    st.caption(
        "Each question is checked independently. Include the product and relevant details "
        "in your message. Verify the cited policy before making a decision."
    )
    prompt = st.chat_input(
        "Ask NexaAssist about a policy…",
        key="policy_chat_input",
        max_chars=2000,
        submit_mode="disable",
    )
    if retry is not None:
        send_question(client, retry)
        st.rerun()
    query = prompt or suggestion
    if query is not None:
        if not query.strip():
            st.warning("Enter a policy question.")
            return
        try:
            request = RetrieveRequest(
                query=query, as_of=as_of, product_id=product_id.strip() or None, top_k=top_k
            )
        except ValidationError:
            st.warning(
                "Enter a policy question of up to 2,000 characters and check the policy scope."
            )
            return
        welcome.empty()
        turn = {
            "id": uuid4().hex,
            "mode": mode,
            "request": request.model_dump(mode="json"),
            "evaluate_quality": evaluate_quality and mode == "Answer",
            "reference_answer": reference_answer.strip() or None,
        }
        st.session_state["clear_quality_reference"] = True
        if conversation is None:
            conversation = {"title": request.query[:48], "turns": turns}
            conversations[identifier] = conversation
        turns.append(turn)
        with st.chat_message("user", avatar=":material/person:"):
            st.markdown(request.query)
        send_question(client, turn)
        st.rerun()
