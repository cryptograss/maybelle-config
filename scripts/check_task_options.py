#!/usr/bin/env python3
"""Every task's options are ones its module takes, before a deploy finds out.

    python3 scripts/check_task_options.py maybelle/ansible/maybelle.yml hunter/ansible/playbook.yml ...

`ansible-playbook --syntax-check` loads a playbook but never asks a module what it takes, so a task with
an option its module doesn't have (`mem_limit` on docker_container, which says `memory`: compose's word,
not the module's) parses fine and fails mid-deploy, after half the play has run (maybelle, 9 Nov). This
reads each playbook and the roles it uses, and checks every task's options against `ansible-doc --json`
for its module, aliases included. Free-form tasks (shell: "...") have no options to check.
Needs ansible (the full package, for the collections' docs).
"""

import json
import subprocess
import sys
from pathlib import Path

import yaml

# What a task may say besides its module (ansible's task and block keywords).
KEYWORDS = {
    'name', 'when', 'loop', 'with_items', 'with_dict', 'with_fileglob', 'with_together', 'with_subelements',
    'with_sequence', 'with_nested', 'with_first_found', 'with_lines', 'with_list', 'loop_control', 'register',
    'notify', 'tags', 'become', 'become_user', 'become_method', 'ignore_errors', 'ignore_unreachable',
    'changed_when', 'failed_when', 'until', 'retries', 'delay', 'no_log', 'environment', 'vars', 'args',
    'delegate_to', 'delegate_facts', 'run_once', 'check_mode', 'diff', 'async', 'poll', 'listen', 'throttle',
    'any_errors_fatal', 'timeout', 'module_defaults', 'collections', 'debugger', 'local_action', 'action',
    'connection', 'remote_user', 'port', 'block', 'rescue', 'always', 'include_tasks', 'import_tasks',
    'include_role', 'import_role', 'include_vars', 'import_playbook', 'become_flags', 'become_exe',
}
_docs = {}


def options(module):
    """{option and alias names} the module takes, or None if ansible-doc doesn't know it."""
    if module not in _docs:
        done = subprocess.run(['ansible-doc', '--json', module], capture_output=True, text=True)
        try:
            doc = next(iter(json.loads(done.stdout).values()))['doc']
        except (ValueError, StopIteration, KeyError, TypeError):
            _docs[module] = None
        else:
            names = set()
            for name, spec in (doc.get('options') or {}).items():
                names.add(name)
                names.update((spec or {}).get('aliases') or [])
            _docs[module] = names
    return _docs[module]


def tasks(items, where):
    for task in items or []:
        if not isinstance(task, dict):
            continue
        for nested in ('block', 'rescue', 'always'):
            yield from tasks(task.get(nested), where)
        yield task, where


def problems(task, where):
    module = next((k for k in task if k not in KEYWORDS), None)
    if module is None:
        return []
    args = task[module]
    if not isinstance(args, dict) or module.split('.')[-1] in ('set_fact', 'set_stats'):
        return []  # free form, or a module whose options are names of the play's own choosing
    takes = options(module)
    if takes is None:
        return []  # not documented here (a role's own module, say): nothing to check against
    takes = takes | {'_raw_params', 'cmd'} if module.endswith(('shell', 'command')) else takes
    wrong = sorted(set(args) - takes)
    return [f"{where}: {task.get('name', module)!r}: {module} takes no {', '.join(wrong)}"] if wrong else []


def check(playbook):
    playbook = Path(playbook)
    found = []
    plays = yaml.safe_load(playbook.read_text()) or []
    for play in plays:
        if not isinstance(play, dict):
            continue
        for part in ('pre_tasks', 'tasks', 'post_tasks', 'handlers'):
            for task, where in tasks(play.get(part), playbook):
                found += problems(task, where)
        for role in play.get('roles') or []:
            name = role if isinstance(role, str) else role.get('role') or role.get('name')
            for roles_dir in (playbook.parent / 'roles', playbook.parent.parent.parent / 'shared' / 'ansible' / 'roles'):
                main = roles_dir / name / 'tasks' / 'main.yml'
                if main.exists():
                    for task, where in tasks(yaml.safe_load(main.read_text()), main):
                        found += problems(task, where)
                    break
    return found


if __name__ == '__main__':
    found = [p for playbook in sys.argv[1:] for p in check(playbook)]
    print('\n'.join(found) or f'every task takes what it says ({len(_docs)} modules checked)')
    sys.exit(1 if found else 0)
