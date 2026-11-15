"""Prepare, load, review and recover saved service-request runs in Streamlit."""

from __future__ import annotations

from datetime import date
from typing import get_args
from uuid import UUID

import httpx
import streamlit as st
from pydantic import ValidationError

from app.models.agent import AgentRequest, AgentResponse
from app.models.agent_run import AgentRunResponse
from app.models.validation import ServiceRequestDetails, ServiceRequestType


def clear_run() -> None:
    """Discard the loaded proposal and prior error when the active credential or run changes."""
    st.session_state.pop("agent_run", None)
    st.session_state.pop("agent_run_error", None)


def clear_conversation() -> None:
    """Clear customer text and loaded results when the operator or conversation changes."""
    clear_run()
    st.session_state.pop("agent_pending_query", None)
    st.session_state.pop("agent_chat_turns", None)


def request_run(
    client: httpx.Client, method: str, path: str, token: str, body: dict | None = None
) -> AgentRunResponse | None:
    """Call a durable-run endpoint and retain either a validated result or a safe UI error.

    Args:
        body: JSON payload for the selected Agent API operation.

    Returns:
        Validated run view, or None after retaining a safe request error.
    """
    clear_run()
    if st.session_state.get("workspace") == "Agent chat":
        path = "/v2" + path
    try:
        response = client.request(
            method, path, headers={"Authorization": "Bearer " + token}, json=body
        )
        if response.status_code == 503 and response.json() == {
            "detail": "Agent run storage unavailable"
        }:
            st.session_state["agent_run_error"] = (
                "Agent run storage is unavailable. Check the Agent API's MongoDB/Atlas connection, "
                "then load any saved run before retrying."
            )
            return None
        response.raise_for_status()
        result = AgentRunResponse.model_validate(response.json())
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        message = {
            401: "Credential rejected. Enter a configured named agent credential.",
            403: "Access denied. Check your named agent credential and its customer access. "
            "Approval requires a different authorized reviewer.",
            404: "Saved run not found or not accessible with this credential. Check the run ID "
            "and use its owner or an authorized reviewer credential.",
            409: "This run cannot perform that operation in its current state. Load the saved "
            "run and check its status and approval expiry before continuing.",
            422: "The request was rejected. Check the customer ID, request details and notes; "
            "remove sensitive identifiers or instruction overrides.",
            503: "The Agent API could not complete the operation. Check the RAG and banking "
            "services and Agent API logs. Load any saved run before retrying.",
        }.get(
            status,
            "The Agent API could not complete the operation. Load any saved run before retrying.",
        )
        st.session_state["agent_run_error"] = f"{message} (HTTP {status})"
        return None
    except httpx.HTTPError:
        st.session_state["agent_run_error"] = (
            "The Agent API connection failed or timed out. Check that the service is running. "
            "Load any saved run before retrying; for an uncertain submission, retry that same run."
        )
        return None
    except ValueError:
        st.session_state["agent_run_error"] = (
            "The Agent API returned an invalid response. Load any saved run before retrying."
        )
        return None
    st.session_state["agent_run"] = result
    return result


def agent_workspace(client: httpx.Client, as_of: date, product_id: str | None) -> None:
    """Render the owner-to-reviewer service-request workflow and safe saved-run recovery.

    Args:
        product_id: Product id identifying the scoped record.

    Example:
        agent_workspace(client, policy_date, selected_product)
    """
    st.title("Policy and service agent", anchor=False)
    st.caption("A different authorized reviewer must approve each proposal before submission.")
    token = st.text_input(
        "Agent credential", type="password", key="agent_token", on_change=clear_conversation
    )
    if not token:
        clear_conversation()
        st.info("Enter your owner or reviewer credential. Customer access is checked by the API.")
        return

    st.caption(
        "Ask a policy question or describe a service request. Reply with missing details "
        "when the agent asks for clarification. Policy scope comes from the sidebar."
    )
    st.button("New agent conversation", on_click=clear_conversation)
    prompt = st.chat_input("Ask about policy or request a service…", max_chars=2000)
    if prompt:
        pending = st.session_state.get("agent_pending_query")
        query = f"{pending}\nClarification: {prompt}" if pending else prompt
        try:
            request = AgentRequest(query=query, as_of=as_of, product_id=product_id)
        except ValidationError:
            st.error(
                "Keep the request and its clarifications within 2,000 characters. "
                "Start a new conversation to restate the complete request."
            )
        else:
            result = request_run(
                client, "POST", "/agent/runs", token, request.model_dump(mode="json")
            )
            if result is not None:
                st.session_state["agent_run_id"] = str(result.run_id)
                if result.response is not None and result.response.status == "needs_clarification":
                    st.session_state["agent_pending_query"] = query
                else:
                    st.session_state.pop("agent_pending_query", None)

    with st.expander("Start a request"):
        with st.form("agent_start"):
            customer = st.text_input("Customer ID", key="agent_customer")
            kind = st.selectbox(
                "Request type", get_args(ServiceRequestType.__value__), key="agent_kind"
            )
            loan = st.text_input("Loan account ID (required for opt-in and certificate)")
            notes = st.text_area("Request notes (optional)", max_chars=500)
            st.caption(f"Policy scope: {as_of.isoformat()} · {product_id or 'all products'}")
            start = st.form_submit_button("Prepare for approval")
        if start:
            try:
                request = AgentRequest(
                    query=f"Create a {kind} service request for the selected customer.",
                    customer_id=customer,
                    as_of=as_of,
                    product_id=product_id,
                    action="createServiceRequest",
                    action_details=ServiceRequestDetails(
                        request_type=kind,
                        loan_account_id=loan.strip() or None,
                        notes=notes.strip() or None,
                    ),
                )
            except ValidationError:
                st.error("Check the customer ID and request details.")
            else:
                clear_conversation()
                result = request_run(
                    client, "POST", "/agent/runs", token, request.model_dump(mode="json")
                )
                if result is not None:
                    st.session_state["agent_run_id"] = str(result.run_id)

    run_id = st.text_input("Saved run ID", key="agent_run_id", on_change=clear_run)
    if st.button("Load saved run"):
        try:
            identifier = UUID(run_id)
        except ValueError:
            clear_run()
            st.error("Enter a valid saved run ID.")
        else:
            clear_conversation()
            request_run(client, "GET", f"/agent/runs/{identifier}", token)

    if error := st.session_state.get("agent_run_error"):
        st.error(error)
    result = st.session_state.get("agent_run")
    if result is None:
        return
    st.write(f"Status: {result.status}")
    st.code(str(result.run_id), language=None)
    st.caption(f"Approval expires: {result.expires_at.isoformat()}")
    if result.response is not None:
        st.write(result.response.message)
        for suggestion in result.response.suggestions:
            st.info(suggestion)
        if result.response.answer is not None:
            for citation in result.response.answer.citations:
                with st.expander(f"[{citation.evidence_id}] {citation.matched_child.title}"):
                    st.text(citation.quote)
                    st.caption(
                        f"{citation.matched_child.doc_id} · "
                        f"v{citation.matched_child.policy_version}"
                    )
        if result.response.validation is not None:
            with st.expander("Proposal and policy evidence", expanded=True):
                st.json(result.response.validation.model_dump(mode="json"))
        if result.response.submission is not None:
            st.success("The bank received this request. Receipt acceptance is not completion.")
            st.json(result.response.submission.model_dump(mode="json"))

    review_run(client, result, token)


def review_run(client: httpx.Client, result: AgentRunResponse, token: str) -> None:
    """Keep exact reviewer approval and uncertain-write recovery shared by both views."""
    if result.status == "pending_approval" and result.proposal_hash is not None:
        st.caption(f"Proposal fingerprint: {result.proposal_hash}")
        with st.form("agent_approval"):
            decision = st.radio("Reviewer decision", ["reject", "approve"], horizontal=True)
            confirmed = st.checkbox("I reviewed this exact proposal and its policy evidence.")
            submit = st.form_submit_button("Submit reviewer decision")
        if submit:
            if not confirmed:
                st.warning("Review the proposal and confirm before submitting a decision.")
            else:
                request_run(
                    client,
                    "POST",
                    f"/agent/runs/{result.run_id}/approval",
                    token,
                    {"decision": decision, "proposal_hash": result.proposal_hash},
                )
                st.rerun()
    elif result.status == "unconfirmed":
        st.warning("The bank may already have received this operation. Keep this saved run.")
        if st.button("Retry the same approved operation"):
            request_run(client, "POST", f"/agent/runs/{result.run_id}/retry", token)
            st.rerun()


def chat_response(response: AgentResponse) -> None:
    """Render policy evidence, banking data and approval-required proposals in a chat turn."""
    st.write(response.message)
    for suggestion in response.suggestions:
        st.info(suggestion)
    if response.answer is not None:
        for citation in response.answer.citations:
            with st.expander(f"[{citation.evidence_id}] {citation.matched_child.title}"):
                st.text(citation.quote)
                st.caption(
                    f"{citation.matched_child.doc_id} · v{citation.matched_child.policy_version}"
                )
    if response.transactions is not None:
        st.dataframe(
            [row.model_dump(mode="json") for row in response.transactions.items], hide_index=True
        )
        if response.transactions.next_cursor is not None:
            st.caption("More transactions exist. This response stops at your requested limit.")
    if response.validation is not None:
        proposal = response.validation.case_proposal or response.validation.proposal
        if proposal is not None:
            with st.expander("Review proposed action", expanded=True):
                st.json(proposal.model_dump(mode="json"))
                st.caption(
                    "A different authorized reviewer must approve this exact proposal before submission."
                )
            with st.expander("Policy evidence for this action"):
                for item in response.validation.evidence:
                    source = item.citation
                    st.caption(
                        f"{source.doc_id} · v{source.policy_version} · clause {source.clause or 'complete instrument'} · effective {source.effective_date}"
                    )
                    st.text(item.quote)
    if response.case_submission is not None:
        st.success(f"Case {response.case_submission.case.case_id} was created.")
    if response.submission is not None:
        st.success(
            f"Service request {response.submission.service_request.service_request_id} was received; completion is pending."
        )


def chat_workspace(client: httpx.Client, as_of: date, product_id: str | None) -> None:
    """Use one prompt for policy, owned transactions, cases and service requests."""
    st.title("NexaAssist", anchor=False)
    st.caption(
        "Ask about loan policies, view your transactions, or prepare a case or service request."
    )
    with st.sidebar.expander("Banking access and approvals", expanded=False):
        token = st.text_input(
            "Agent credential", type="password", key="agent_token", on_change=clear_conversation
        )
        st.caption(
            "Policy questions work without a credential. Banking reads and proposals require authorized customer access."
        )
        run_id = st.text_input("Saved run ID", key="agent_run_id")
        load = st.button("Load saved run")
    turns = st.session_state.setdefault("agent_chat_turns", [])
    if load:
        try:
            identifier = UUID(run_id)
        except ValueError:
            st.error("Enter a valid saved run ID.")
        else:
            result = request_run(client, "GET", f"/agent/runs/{identifier}", token)
            if result is not None:
                turns.append({"prompt": None, "result": result})

    if not turns:
        st.markdown(
            "Try **Maximum loan amount for NexaHome**, **Eligibility for auto loan**, "
            "**Get at most five transactions from 1–14 November 2026**, or "
            "**Prepare a LOW-priority LOAN_ENQUIRY case for NXC-0001 about home-loan documents**."
        )
    current = st.session_state.get("agent_run")
    for turn in turns:
        if turn["prompt"] is not None:
            with st.chat_message("user"):
                st.write(turn["prompt"])
        value = turn.get("result")
        if (
            isinstance(value, AgentRunResponse)
            and current is not None
            and value.run_id == current.run_id
        ):
            value = turn["result"] = current
        with st.chat_message("assistant"):
            response = value.response if isinstance(value, AgentRunResponse) else value
            if response is not None:
                chat_response(response)
            if turn.get("error"):
                st.error(turn["error"])
            if isinstance(value, AgentRunResponse) and value.status in (
                "pending_approval",
                "unconfirmed",
            ):
                st.caption(f"Status: {value.status} · Saved run: {value.run_id}")

    if error := st.session_state.get("agent_run_error"):
        st.error(error)
    if current is not None:
        review_run(client, current, token)
    prompt = st.chat_input(
        "Ask NexaAssist anything about your banking or policies…",
        max_chars=2000,
        key="agent_chat_input",
    )
    if prompt:
        pending = st.session_state.get("agent_pending_query")
        query = f"{pending}\nClarification: {prompt}" if pending else prompt
        try:
            request = AgentRequest(query=query, as_of=as_of, product_id=product_id)
        except ValidationError:
            st.error(
                "Keep the request and clarifications within 2,000 characters. Start a new chat to restate it."
            )
            return
        result_value: AgentRunResponse | AgentResponse | None = None
        with st.spinner("Checking your request…"):
            if token:
                result_value = request_run(
                    client, "POST", "/agent/runs", token, request.model_dump(mode="json")
                )
            else:
                clear_run()
                try:
                    reply = client.post("/v2/agent", json=request.model_dump(mode="json"))
                    reply.raise_for_status()
                    result_value = AgentResponse.model_validate(reply.json())
                except httpx.HTTPError, ValueError:
                    st.session_state["agent_run_error"] = (
                        "The agent could not complete this request. Check the Agent API and try again."
                    )
        turns.append(
            {
                "prompt": prompt,
                "result": result_value,
                "error": st.session_state.get("agent_run_error"),
            }
        )
        response = (
            result_value.response if isinstance(result_value, AgentRunResponse) else result_value
        )
        if response is not None and response.status == "needs_clarification":
            st.session_state["agent_pending_query"] = query
        elif response is not None:
            st.session_state.pop("agent_pending_query", None)
        st.rerun()
