"""Explicit offline/local-model composition; secure key reuse is main-entry opt-in."""
from __future__ import annotations

import argparse
import json
import sys
import time

from pacttrail.application.chat import ChatSession, chat_error, safe_chat_error
from pacttrail.chat_web import serve_chat
from pacttrail.domain.evidence import SampleReadBinding, sample_read_manifest, sample_read_policy
from pacttrail.platform.chat_catalog import model_catalog
from pacttrail.platform.conversation_store import ConversationStore
from pacttrail.platform.evidence_files import SampleEvidenceReader


def add_chat_parser(commands):
    chat = commands.add_parser('chat', help='chat with the offline synthetic demo', allow_abbrev=False)
    actions = chat.add_subparsers(dest='chat_command', required=True)
    actions.add_parser('models', help='list model candidates without connecting', allow_abbrev=False)
    serve = actions.add_parser('serve', help='open the local offline conversation', allow_abbrev=False)
    serve.add_argument('--sample', action='store_true', required=True)
    modes = serve.add_mutually_exclusive_group()
    modes.add_argument('--offline-demo', action='store_true', help='use only fixed demonstration answers')
    modes.add_argument('--local-model', action='store_true', help='enable explicit in-memory model configuration')
    modes.add_argument('--agent-demo', action='store_true', help='synthetic decisions with actual task file tools')
    modes.add_argument('--agent-protocol-demo', action='store_true',
                       help='synthetic model tool protocol with actual task file tools')
    modes.add_argument('--agent-model', action='store_true',
                       help='explicit in-memory model connection for bounded task file tools')
    modes.add_argument('--task-history-dir', help='read-only view of an existing task directory; never resume execution')
    serve.add_argument('--task-dir', help='new dedicated synthetic project and report directory')
    serve.add_argument('--edit-project-dir', help='allow reviewed edits in this explicitly selected project directory')
    serve.add_argument('--followup-from', help='explicit read-only parent task directory for new follow-up tasks')
    serve.add_argument('--web-research', action='store_true', help='opt in to explicit public-page acquisition before task send')
    serve.add_argument('--answer-mode', choices=('evidence', 'ordinary'), default=argparse.SUPPRESS,
                       help='response contract; ordinary accepts plain text, offline-demo stays synthetic')
    serve.add_argument('--history-dir', help='save bounded conversations in this dedicated local directory')
    serve.add_argument('--port', type=int, default=0, help='127.0.0.1 port; default chooses a free port')


def _write_catalog():
    result = json.dumps({'live_available': False, 'providers': model_catalog()}, ensure_ascii=True) + '\n'
    try:
        binary = getattr(sys.stdout, 'buffer', None)
        if binary is None:
            written = sys.stdout.write(result)
        else:
            written = getattr(binary, 'raw', binary).write(result.encode('ascii'))
        if written != len(result):
            raise chat_error('state_unavailable')
        sys.stdout.flush()
    except Exception:
        raise chat_error('state_unavailable') from None
    return 0


def run_chat(args):
    try:
        if args.chat_command == 'models':
            return _write_catalog()
        if args.chat_command != 'serve' or args.sample is not True:
            raise chat_error('invalid_request')
        local_model = getattr(args, 'local_model', False)
        agent_demo = getattr(args, 'agent_demo', False)
        agent_protocol = getattr(args, 'agent_protocol_demo', False)
        agent_model = getattr(args, 'agent_model', False)
        remember_model_key = getattr(args, 'remember_model_key', False)
        task_history = getattr(args, 'task_history_dir', None)
        followup_from = getattr(args, 'followup_from', None)
        web_research = getattr(args, 'web_research', False)
        search_provider = getattr(args, 'search_provider', 'bing')
        edit_project = getattr(args, 'edit_project_dir', None)
        capacity = getattr(args, 'ordinary_capacity', None)
        conversation_capacity = getattr(args, 'conversation_capacity', None)
        if (type(search_provider) is not str or search_provider not in ('bing', 'deepseek')
                or search_provider == 'deepseek' and (not agent_model or getattr(args, 'agent_workspace', None) is None)):
            raise chat_error('invalid_request')
        if type(remember_model_key) is not bool:
            raise chat_error('invalid_request')
        if remember_model_key:
            from pacttrail.platform.agent_workspace import AgentWorkspace
            if not agent_model or type(getattr(args, 'agent_workspace', None)) is not AgentWorkspace:
                raise chat_error('invalid_request')
        if capacity is not None:
            from pacttrail.domain.conversation_capacity import OrdinaryCapacity
            from pacttrail.platform.agent_workspace import AgentWorkspace
            if type(capacity) is not OrdinaryCapacity or type(getattr(args, 'agent_workspace', None)) is not AgentWorkspace:
                raise chat_error('invalid_request')
        if conversation_capacity is not None:
            from pacttrail.domain.conversation_capacity import ConversationCapacity
            from pacttrail.platform.agent_workspace import AgentWorkspace
            if (type(conversation_capacity) is not ConversationCapacity
                    or type(getattr(args, 'agent_workspace', None)) is not AgentWorkspace):
                raise chat_error('invalid_request')
        if edit_project is not None and not (agent_demo or agent_protocol or agent_model):
            raise chat_error('invalid_request')
        if type(web_research) is not bool or web_research and not (agent_demo or agent_protocol or agent_model):
            raise chat_error('invalid_request')
        if followup_from is not None and not (agent_demo or agent_protocol or agent_model):
            raise chat_error('invalid_request')
        if (type(local_model) is not bool or type(agent_demo) is not bool
                or type(agent_protocol) is not bool or type(agent_model) is not bool
                or sum((local_model, agent_demo, agent_protocol, agent_model, bool(args.offline_demo),
                        task_history is not None)) > 1):
            raise chat_error('invalid_request')
        if getattr(args, 'task_dir', None) is not None and not (agent_demo or agent_protocol or agent_model):
            raise chat_error('invalid_request')
        if not any((args.offline_demo is True, local_model, agent_demo, agent_protocol, agent_model,
                    task_history is not None)):
            raise chat_error('live_scope_required')
        if type(args.port) is not int or not 0 <= args.port <= 65535:
            raise chat_error('invalid_request')
        answer_mode = getattr(args, 'answer_mode', 'evidence')
        if type(answer_mode) is not str or answer_mode not in ('evidence', 'ordinary'):
            raise chat_error('invalid_request')
        task_dir = getattr(args, 'task_dir', None)
        history_dir = getattr(args, 'history_dir', None)
        if task_history is not None:
            if task_dir is not None or hasattr(args, 'answer_mode') or getattr(args, 'history_dir', None) is not None:
                raise chat_error('invalid_request')
            session = create_task_history_session(task_history)
            try:
                return serve_chat(args, session)
            finally:
                session.close()
        if agent_demo or agent_protocol or agent_model:
            if (task_dir is None or hasattr(args, 'answer_mode')
                    or history_dir is not None and (followup_from is not None or web_research)):
                raise chat_error('invalid_request')
            if history_dir is not None:
                _preflight_tool_history_roots(task_dir, history_dir, edit_project)
            if edit_project is not None:
                from pacttrail.platform.draft_apply_files import preflight_edit_roots
                try:
                    preflight_edit_roots(edit_project, task_dir)
                except Exception:
                    raise chat_error('invalid_request') from None
            from pacttrail.application.tool_task import ToolTaskSession
            from pacttrail.platform.task_files import TaskFiles
            web_options = {}
            if search_provider == 'deepseek':
                web_options['search_provider'] = search_provider
            if web_research:
                from pacttrail.platform.web_fetch import WebFetcher
                web_options.update(web_fetcher_factory=WebFetcher, web_transport='http')
            # Validate the saved conversation before allocating a new task root.
            conversation = (_open_tool_conversation(history_dir, capacity=conversation_capacity)
                            if history_dir is not None else None)
            session = None
            try:
                if followup_from is None:
                    session = (create_tool_model_session(task_dir, **web_options) if agent_model else
                               create_tool_protocol_session(task_dir, **web_options) if agent_protocol else
                               ToolTaskSession(TaskFiles(task_dir), **web_options))
                else:
                    if agent_model:
                        session = create_tool_model_session(task_dir, followup_from=followup_from, **web_options)
                    elif agent_protocol:
                        session = create_tool_protocol_session(task_dir, followup_from=followup_from, **web_options)
                    else:
                        parent_store = open_followup_store(task_dir, followup_from)
                        session = ToolTaskSession(TaskFiles(task_dir), followup_store=parent_store, **web_options)
                if conversation is not None:
                    session.enable_conversation(conversation, ordinary_capacity=capacity)
                workspace = getattr(args, 'agent_workspace', None)
                if workspace is not None:
                    from pacttrail.platform.agent_workspace import AgentWorkspace
                    if type(workspace) is not AgentWorkspace or workspace.task_dir != task_dir:
                        raise chat_error('invalid_request')
                    from pacttrail.platform.web_search import create_search_research
                    session.enable_web_search(create_search_research)
                    if remember_model_key:
                        from pacttrail.platform.model_credentials import WindowsModelCredentialStore
                        session.enable_key_store(WindowsModelCredentialStore(workspace.root))
                if edit_project is not None:
                    session = (enable_project_edit(session, task_dir, edit_project, workspace=workspace)
                               if workspace is not None else enable_project_edit(session, task_dir, edit_project))
                if workspace is not None:
                    from pacttrail.application.agent_task_history import AgentTaskHistorySession
                    session = AgentTaskHistorySession(session, workspace)
                return serve_chat(args, session)
            finally:
                try:
                    if session is not None:
                        session.close()
                finally:
                    if conversation is not None:
                        conversation.close()
        if task_dir is not None:
            raise chat_error('invalid_request')
        if local_model:
            if getattr(args, 'history_dir', None) is not None:
                raise chat_error('invalid_request')
            session = create_local_model_session(answer_mode=answer_mode)
            try:
                return serve_chat(args, session)
            finally:
                session.close()
        history = ConversationStore(args.history_dir) if getattr(args, 'history_dir', None) is not None else None
        try:
            binding = (None if answer_mode == 'ordinary' else
                       SampleReadBinding(sample_read_manifest(), SampleEvidenceReader()))
            session = ChatSession(binding, sample_read_policy(), model_catalog(), history=history,
                                  answer_mode=answer_mode)
        except Exception:
            if history is not None:
                history.close()
            raise
        try:
            return serve_chat(args, session)
        finally:
            session.close()
    except Exception as error:
        raise safe_chat_error(error) from None


def open_followup_store(child_root, parent_root):
    """Validate a read-only parent and disjoint roots before creating child files."""
    if parent_root is None:
        return None
    import os
    from pathlib import Path
    from pacttrail.platform.task_record_store import TaskRecordStore
    try:
        store = TaskRecordStore(parent_root)
        child = Path(child_root)
        if not child.is_absolute() or '..' in child.parts:
            raise ValueError('invalid child root')
        parent_name = os.path.normcase(str(store.root.resolve()))
        child_name = os.path.normcase(str(child.resolve()))
        try:
            common = os.path.commonpath((parent_name, child_name))
        except ValueError:  # Different local volumes cannot contain each other.
            common = None
        if common in (parent_name, child_name):
            raise ValueError('overlapping task roots')
        return store
    except Exception:
        raise chat_error('history_unavailable') from None


def _preflight_tool_history_roots(task_dir, history_dir, edit_project=None):
    """Reject directory conflicts and links before any SQLite or task creation."""
    import os
    import stat
    from pacttrail.domain.draft_apply import target_path
    try:
        task, history = target_path(os.fspath(task_dir)), target_path(os.fspath(history_dir))
        roots = [task, history]
        if edit_project is not None:
            roots.append(target_path(os.fspath(edit_project)))
        for index, left in enumerate(roots):
            for right in roots[index + 1:]:
                if left == right or left in right.parents or right in left.parents:
                    raise ValueError('overlapping roots')
        for root in roots:
            for path in reversed((root, *root.parents)):
                try:
                    meta = path.lstat()
                except FileNotFoundError:
                    continue
                if (stat.S_ISLNK(meta.st_mode) or getattr(meta, 'st_file_attributes', 0) & 0x400
                        or not stat.S_ISDIR(meta.st_mode)):
                    raise ValueError('nonplain directory')
        # A task root is always newly reserved; do not open/save history first
        # when its eventual creation is already known to be impossible.
        if not task.parent.is_dir():
            raise ValueError('missing task parent')
        try:
            task.lstat()
        except FileNotFoundError:
            pass
        else:
            raise ValueError('existing task root')
    except Exception:
        raise chat_error('invalid_request') from None


def _open_tool_conversation(history_dir, *, capacity=None):
    from pacttrail.application.tool_conversation import ToolConversation
    store = ConversationStore(history_dir)
    try:
        return ToolConversation(store, capacity=capacity)
    except Exception:
        store.close()
        raise


def enable_tool_conversation(session, history_dir, *, conversation_capacity=None):
    """Attach validated dialogue to an existing trusted tool-session assembly."""
    conversation = _open_tool_conversation(history_dir, capacity=conversation_capacity)
    try:
        session.enable_conversation(conversation)
        return session
    except Exception:
        conversation.close()
        raise


def create_task_history_session(task_dir):
    """Open only this explicitly selected existing root; never restore execution."""
    from pacttrail.application.task_history import TaskHistorySession
    from pacttrail.platform.task_record_store import TaskRecordStore
    try:
        return TaskHistorySession(TaskRecordStore(task_dir))
    except Exception:
        raise chat_error('history_unavailable') from None


def enable_project_edit(session, task_dir, project_dir, *, workspace=None):
    """The only composition path granting reviewed writes to a fixed project root."""
    from pacttrail.application.draft_apply import DraftApplyService
    from pacttrail.application.editable_task import EditableTaskSession
    from pacttrail.platform.draft_apply_files import DraftApplyFiles
    from pacttrail.platform.project_verifier import ProjectVerifier
    from pacttrail.platform.project_locator import ProjectLocator
    from pacttrail.platform.task_record_store import TaskRecordStore

    try:
        resolver = None
        if workspace is not None:
            from pacttrail.application.saved_draft import SavedDraftResolver
            resolver = SavedDraftResolver(workspace, project_dir)
        return EditableTaskSession(session, DraftApplyService(
            TaskRecordStore(task_dir), DraftApplyFiles(project_dir, task_dir)), ProjectVerifier(),
            saved_draft_resolver=resolver, project_locator=ProjectLocator(project_dir))
    except Exception:
        raise chat_error('invalid_request') from None


def create_local_model_session(*, answer_mode='evidence', transport_factory=None,
                               clock=time.monotonic, delay_seconds=0.35):
    """Same installed assembly for real transport and trusted synthetic acceptance."""
    if type(answer_mode) is not str or answer_mode not in ('evidence', 'ordinary'):
        raise chat_error('invalid_request')
    from pacttrail.application.local_model_chat import LocalModelChat
    from pacttrail.platform.deepseek_admission import DeepSeekAdmission
    from pacttrail.platform.deepseek_protocol import DeepSeekOrdinaryCompletion, DeepSeekProtocolCompletion
    from pacttrail.platform.deepseek_tokens import DeepSeekTokenEstimator

    if transport_factory is None:
        from pacttrail.platform.deepseek_http import DeepSeekHTTPTransport
        transport_factory = lambda key: DeepSeekHTTPTransport(key, clock=clock)
    if not callable(transport_factory):
        raise chat_error('invalid_request')
    guard = DeepSeekAdmission(estimate_tokens=DeepSeekTokenEstimator(request_mode=answer_mode))
    binding = (None if answer_mode == 'ordinary' else
               SampleReadBinding(sample_read_manifest(), SampleEvidenceReader()))

    def make_session(**kwargs):
        return ChatSession(binding, sample_read_policy(), model_catalog(),
                           clock=clock, delay_seconds=delay_seconds, answer_mode=answer_mode, **kwargs)

    def make_completion(key):
        transport = transport_factory(key)
        try:
            if answer_mode == 'ordinary':
                return DeepSeekOrdinaryCompletion(transport, clock=clock, admission=guard,
                                                  completion_timeout=60), transport
            return DeepSeekProtocolCompletion(transport, clock=clock, admission=guard), transport
        except Exception:
            transport.close()
            raise

    return LocalModelChat(make_session, make_completion,
                          lambda: {**guard.status(), **guard.metrics()}, answer_mode=answer_mode)


def create_tool_protocol_session(task_dir, *, transport_factory=None, clock=time.monotonic,
                                 delay_seconds=0.05, completion_timeout=5,
                                 budget_nano_usd=100_000_000, followup_from=None,
                                 web_fetcher_factory=None, web_transport='synthetic'):
    """Offline installed assembly. Each run owns one synthetic transport.

    Factories are trusted test injection only; there is no key or live selector.
    Admission is process-owned and deliberately survives task reset/retirement.
    """
    import math
    from pacttrail.application.tool_task import ToolTaskSession
    from pacttrail.platform.deepseek_admission import DeepSeekToolAdmission
    from pacttrail.platform.deepseek_tokens import DeepSeekTokenEstimator
    from pacttrail.platform.deepseek_tool_protocol import DeepSeekToolCompletion
    from pacttrail.platform.synthetic_tool_transport import SyntheticToolTransport
    from pacttrail.platform.task_files import TaskFiles

    if (not callable(clock) or (transport_factory is not None and not callable(transport_factory))
            or type(completion_timeout) not in (int, float)
            or not math.isfinite(completion_timeout) or not 0 < completion_timeout <= 60
            or type(delay_seconds) not in (int, float) or not 0 <= delay_seconds <= 1):
        raise chat_error('invalid_request')
    parent_store = open_followup_store(task_dir, followup_from)
    estimator = DeepSeekTokenEstimator(request_mode='tools')
    guard = DeepSeekToolAdmission(estimate_tokens=estimator, budget_nano_usd=budget_nano_usd)
    if transport_factory is None:
        transport_factory = lambda: SyntheticToolTransport(estimator)

    def make_decider(run_id, deadline, cancel):
        transport = transport_factory()
        try:
            return DeepSeekToolCompletion(transport, admission=guard, run_id=run_id,
                                          deadline=deadline, cancel=cancel, clock=clock,
                                          completion_timeout=completion_timeout)
        except Exception:
            close = getattr(transport, 'close', None)
            if callable(close):
                close()
            raise

    return ToolTaskSession(TaskFiles(task_dir), decision_factory=make_decider,
                           model_status=lambda: {**guard.status(), **guard.metrics()},
                           preview_request=guard.preview,
                           clock=clock, delay_seconds=delay_seconds,
                           completion_timeout=completion_timeout, followup_store=parent_store,
                           web_fetcher_factory=web_fetcher_factory, web_transport=web_transport)


def create_tool_model_session(task_dir, *, transport_factory=None, clock=time.monotonic,
                              delay_seconds=0.05, budget_nano_usd=100_000_000, followup_from=None,
                              web_fetcher_factory=None, web_transport='synthetic',
                              search_provider='bing', search_factory=None, search_page_factory=None):
    """Same active connection assembly for live HTTP and trusted synthetic tests.

    Construction/configuration/activation never send a model request. Credentials
    enter only through the existing page action; no key discovery or CLI input.
    """
    import math
    from pacttrail.application.local_tool_model import LocalToolModelChat
    from pacttrail.platform.deepseek_admission import DeepSeekToolAdmission
    from pacttrail.platform.deepseek_tokens import DeepSeekTokenEstimator
    from pacttrail.platform.deepseek_tool_protocol import DeepSeekToolCompletion
    from pacttrail.platform.task_files import TaskFiles

    if (not callable(clock) or (transport_factory is not None and not callable(transport_factory))
            or type(search_provider) is not str or search_provider not in ('bing', 'deepseek')
            or search_factory is not None and not callable(search_factory)
            or search_page_factory is not None and not callable(search_page_factory)
            or search_provider == 'bing' and (search_factory is not None or search_page_factory is not None)
            or type(delay_seconds) not in (int, float) or not math.isfinite(delay_seconds)
            or not 0 <= delay_seconds <= 1):
        raise chat_error('invalid_request')
    parent_store = open_followup_store(task_dir, followup_from)
    transport_mode = 'live' if transport_factory is None else 'synthetic'
    if transport_factory is None:
        from pacttrail.platform.deepseek_http import DeepSeekHTTPTransport
        transport_factory = lambda key: DeepSeekHTTPTransport(key, clock=clock)
    guard = DeepSeekToolAdmission(estimate_tokens=DeepSeekTokenEstimator(request_mode='tools'),
                                  budget_nano_usd=budget_nano_usd)

    make_search = None
    if search_provider == 'deepseek':
        from pacttrail.application.search_research import SearchResearch
        from pacttrail.platform.deepseek_search import DeepSeekSearcher
        from pacttrail.platform.search_page import SearchPageReader
        factory = DeepSeekSearcher if search_factory is None else search_factory
        page_factory = SearchPageReader if search_page_factory is None else search_page_factory

        def make_search(key, *, before_dispatch, after_dispatch, register):
            def search_adapter():
                adapter = factory(key, admission=guard, before_dispatch=before_dispatch,
                    after_dispatch=after_dispatch, clock=clock)
                register(adapter)
                return adapter
            return SearchResearch(search_adapter, page_factory, clock=clock, query_limit=128)

    def make_completion(key, run_id, deadline, cancel):
        transport = transport_factory(key)
        try:
            if not callable(getattr(transport, 'status', None)):
                raise chat_error('invalid_request')
            return DeepSeekToolCompletion(transport, admission=guard, run_id=run_id,
                                          deadline=deadline, cancel=cancel, clock=clock,
                                          completion_timeout=60), transport
        except Exception:
            close = getattr(transport, 'close', None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass
            raise

    return LocalToolModelChat(TaskFiles(task_dir), make_completion,
                              lambda: {**guard.status(), **guard.metrics()},
                              preview_request=guard.preview,
                              resume_admission=guard.resume_known,
                              transport_mode=transport_mode, clock=clock,
                              delay_seconds=delay_seconds, followup_store=parent_store,
                               web_fetcher_factory=web_fetcher_factory, web_transport=web_transport,
                               make_search=make_search)
