"""A single entry into the existing persistent project conversation."""
from __future__ import annotations

from types import SimpleNamespace

from pacttrail.application.chat import chat_error, safe_chat_error
from pacttrail import chat_cli
from pacttrail.domain.conversation_capacity import OrdinaryCapacity, ConversationCapacity
from pacttrail.platform.agent_workspace import AgentWorkspace


def add_agent_parser(commands):
    agent = commands.add_parser('agent', help='start or reopen a persistent project conversation', allow_abbrev=False)
    agent.add_argument('--workspace-dir', required=True, help='dedicated absolute state directory; manages tasks and history')
    agent.add_argument('--edit-project-dir', help='explicit fixed project for reviewed writes; repeat the same root when reopening')
    agent.add_argument('--local-model', action='store_true', help='opt into the existing in-memory model configuration page')
    agent.add_argument('--search-provider', choices=('bing', 'deepseek'), default='bing',
                       help='optional search service; deepseek requires --local-model and charges successful searches')
    agent.add_argument('--remember-model-key', action='store_true',
                       help='with --local-model, save and restore this workspace key in Windows Credential Manager')
    agent.add_argument('--ordinary-input-chars', type=int, default=2048,
                       help='ordinary question Unicode characters, 1-4096 (default 2048); repeat on reopen')
    agent.add_argument('--ordinary-answer-chars', type=int, default=2048,
                       help='ordinary answer Unicode characters, 1-4096 (default 2048); repeat on reopen')
    agent.add_argument('--conversation-max-turns', type=int, default=12,
                       help='attempts per conversation, 1-32 (default 12); process request budget stays 24')
    agent.add_argument('--conversation-context-kib', type=int, default=16,
                       help='complete successful history in KiB, 1-64 (default 16); overflow refuses without truncation')
    agent.add_argument('--port', type=int, default=0, help='127.0.0.1 port; default chooses a free port')


def run_agent(args):
    try:
        remember_model_key = getattr(args, 'remember_model_key', False)
        search_provider = getattr(args, 'search_provider', 'bing')
        if (type(args.local_model) is not bool or type(remember_model_key) is not bool
                or remember_model_key and not args.local_model
                or type(search_provider) is not str or search_provider not in ('bing', 'deepseek')
                or search_provider == 'deepseek' and not args.local_model):
            raise chat_error('invalid_request')
        if type(args.port) is not int or not 0 <= args.port <= 65535:
            raise chat_error('invalid_request')
        try:
            capacity = OrdinaryCapacity(getattr(args, 'ordinary_input_chars', 2048),
                                        getattr(args, 'ordinary_answer_chars', 2048))
            context_kib = getattr(args, 'conversation_context_kib', 16)
            if type(context_kib) is not int:
                raise ValueError()
            conversation_capacity = ConversationCapacity(getattr(args, 'conversation_max_turns', 12), context_kib * 1024)
        except ValueError:
            raise chat_error('invalid_request') from None
        with AgentWorkspace(args.workspace_dir, args.edit_project_dir) as workspace:
            routed = SimpleNamespace(chat_command='serve', sample=True, offline_demo=False, port=args.port,
                agent_protocol_demo=not args.local_model, agent_model=args.local_model,
                task_dir=workspace.task_dir, history_dir=workspace.history_dir,
                edit_project_dir=workspace.edit_project_dir, agent_workspace=workspace,
                remember_model_key=remember_model_key,
                search_provider=search_provider,
                ordinary_capacity=capacity, conversation_capacity=conversation_capacity)
            return chat_cli.run_chat(routed)
    except Exception as error:
        raise safe_chat_error(error) from None
