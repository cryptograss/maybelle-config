#!/usr/bin/env python3
"""What a change to the deploy logic renders differently, before it deploys.

    python3 scripts/render_diff.py                      # this tree against origin/production
    python3 scripts/render_diff.py --base <ref>         # against another ref
    python3 scripts/render_diff.py --head <ref>         # a ref instead of this tree

Every server's playbook and roles are rendered from both trees, and what
differs is printed: every *.j2 template, and every string with Jinja in it in
every playbook and task file (a `copy: content:`, an .env, a command). Found,
not listed: a list of "the templates that matter" goes stale the first time a
change reaches a reader nobody listed (maybelle-config#224 found two that
way: ssh-people.j2 and post-deploy-log.py).

A role included in a loop is rendered once for each item, with the call's own
`vars:` (hunter's user-instance, once per container), so a change in how a
role is called shows up in what it renders.

Safe to run anywhere: nothing is executed and no secret is read. The vault is
never opened; a variable nothing here defines renders as ‹?name›, the same in
both trees, so only real changes differ. `lookup('file', ...)` reads files
inside the tree; every other lookup (pipe, env, password...) renders as
‹lookup:kind›. Rendered with plain Jinja2 and Ansible's own filters and tests,
so it needs ansible-core and PyYAML, not a host.

What it can't see: a role's tasks run in a loop of their own (`item` is
‹?item›), when: conditions, and anything decided on the server. "Identical"
here means the deploy logic says the same thing, not that the server will
end up the same.

Exit status: 0 when it rendered, whatever differs; 1 if a render fails with
an error in the new tree that it didn't have in the old one.
"""

import argparse
import difflib
import os
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

os.environ.setdefault('ANSIBLE_DEPRECATION_WARNINGS', 'False')  # its filters warn about ‹?name›

import jinja2
import yaml

SERVERS = ('hunter', 'maybelle', 'delivery-kid', 'pickipedia-vps')
JINJA = ('{{', '{%')
MAX_DEPTH = 8  # inventory values that template other values, resolved this deep


class Placeholder(jinja2.ChainableUndefined):
    """An undefined variable, rendered as its name: the same in both trees."""

    def __str__(self):
        return f'‹?{self._undefined_name or "undefined"}›'

    def __iter__(self):
        return iter(())

    def __len__(self):
        return 0


def environment(tree):
    from ansible.plugins.filter.core import FilterModule as Core
    from ansible.plugins.filter.mathstuff import FilterModule as Math
    from ansible.plugins.filter.urls import FilterModule as Urls
    from ansible.plugins.test.core import TestModule as Tests
    from ansible.plugins.test.files import TestModule as FileTests
    from ansible.plugins.test.mathstuff import TestModule as MathTests

    env = jinja2.Environment(undefined=Placeholder, extensions=['jinja2.ext.do', 'jinja2.ext.loopcontrols'],
                             keep_trailing_newline=True, trim_blocks=True)
    for module in (Core, Math, Urls):
        # Jinja's own default/d: Ansible's knows only Ansible's undefined, not ‹?name›.
        env.filters.update({k: f for k, f in module().filters().items() if k not in ('default', 'd')})
    for module in (Tests, FileTests, MathTests):
        env.tests.update(module().tests())

    def lookup(kind, *terms, **options):
        if kind != 'file':
            return f'‹lookup:{kind}›'
        out = []
        for term in terms:
            path = Path(str(term)).resolve()
            if tree.resolve() not in path.parents:
                out.append('‹lookup:file outside the tree›')
                continue
            try:
                out.append(path.read_text().rstrip('\n'))
            except OSError:
                out.append(f'‹lookup:file missing {path.relative_to(tree.resolve())}›')
        return ','.join(out)

    env.globals['lookup'] = lookup
    env.globals['tree'] = str(tree.resolve())
    env.globals['query'] = lambda kind, *terms, **options: [lookup(kind, t) for t in terms]
    return env


def render(env, text, context):
    try:
        out = env.from_string(text).render(**context)
    except Exception as e:  # noqa: BLE001 -- a render that fails is a result, not a crash
        out = f'‹error: {type(e).__name__}: {e}›'
    tree = env.globals.get('tree')
    return out.replace(tree, '<tree>') if tree else out  # the same path in both trees


def resolved(env, value, context, depth=0):
    """A value with its own Jinja rendered against the context (inventory values that name others)."""
    if depth > MAX_DEPTH:
        return value
    if isinstance(value, str) and any(j in value for j in JINJA):
        return render(env, value, context)
    if isinstance(value, dict):
        return {k: resolved(env, v, context, depth + 1) for k, v in value.items()}
    if isinstance(value, list):
        return [resolved(env, v, context, depth + 1) for v in value]
    return value


def load_yaml(path):
    try:
        return yaml.safe_load(path.read_text())
    except (OSError, yaml.YAMLError):
        return None


def inventory_vars(server_dir):
    """all.vars and every host's vars, merged: what the playbooks see (one host each, here)."""
    inv = load_yaml(server_dir / 'ansible' / 'inventory.yml') or {}
    group = inv.get('all') or {}
    found = dict(group.get('vars') or {})
    for host, host_vars in (group.get('hosts') or {}).items():
        found.setdefault('inventory_hostname', host)
        found.update(host_vars or {})
    return found


def strings(node, path=''):
    """(path, string) for every string with Jinja in it, in a YAML document."""
    if isinstance(node, str):
        if any(j in node for j in JINJA):
            yield path, node
    elif isinstance(node, dict):
        label = node.get('name') if isinstance(node.get('name'), str) else None
        role_call = any(k in node for k in ('include_role', 'import_role', 'ansible.builtin.include_role'))
        for key, value in node.items():
            if role_call and key in ('vars', 'loop', 'with_items'):
                continue  # rendered once per item, with the role (renders)
            yield from strings(value, f'{path}/{label}:{key}' if label and key != 'name' else f'{path}/{key}')
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from strings(value, f'{path}[{i}]')


def role_dirs(tree, server_dir, name):
    for base in (server_dir / 'ansible' / 'roles', tree / 'shared' / 'ansible' / 'roles'):
        if (base / name).is_dir():
            return base / name
    return None


def calls(playbook):
    """(role name, the include_role task) for each role a playbook includes, in order."""
    for play in playbook or ():
        for task in (play.get('tasks') or []) + (play.get('pre_tasks') or []) + (play.get('post_tasks') or []):
            if not isinstance(task, dict):
                continue
            include = task.get('include_role') or task.get('import_role') or task.get('ansible.builtin.include_role')
            if isinstance(include, dict) and include.get('name'):
                yield include['name'], task
        for role in play.get('roles') or ():
            yield (role if isinstance(role, str) else role.get('role') or role.get('name')), {}


def render_role(env, tree, role_dir, context, label, out):
    defaults = {}
    for part in ('defaults', 'vars'):
        defaults.update(load_yaml(role_dir / part / 'main.yml') or {})
    ctx = {**context, 'role_path': str(role_dir)}
    ctx.update({k: v for k, v in resolved(env, defaults, ctx).items() if k not in context})
    for path in sorted(role_dir.rglob('*')):
        rel = path.relative_to(tree)
        if path.suffix == '.j2':
            out[f'{label}{rel}'] = render(env, path.read_text(), ctx)
        elif path.suffix in ('.yml', '.yaml') and path.parent.name in ('tasks', 'handlers'):
            for where, text in strings(load_yaml(path)):
                out[f'{label}{rel}{where}'] = render(env, text, ctx)


def renders(tree):
    """{what: rendered text} for every template and Jinja string the servers' playbooks reach."""
    tree = Path(tree)
    env = environment(tree)
    out = {}
    for server in SERVERS:
        server_dir = tree / server
        playbooks = [p for p in sorted((server_dir / 'ansible').glob('*.yml'))
                     if p.name not in ('inventory.yml', 'vault.yml') and not p.name.startswith('inventory')]
        base = inventory_vars(server_dir)
        for playbook_path in playbooks:
            playbook = load_yaml(playbook_path)
            if not isinstance(playbook, list):
                continue
            context = {'playbook_dir': str(playbook_path.parent), 'groups': {'all': [base.get('inventory_hostname', server)]},
                       **base}
            for play in playbook:
                if isinstance(play, dict):
                    context.update(play.get('vars') or {})
            context = {**context, **resolved(env, context, context)}
            for where, text in strings(playbook):
                out[f'{playbook_path.relative_to(tree)}{where}'] = render(env, text, context)
            for path in sorted((server_dir / 'ansible').glob('*.j2')) + sorted((server_dir / 'ansible').rglob('conf-templates/*.j2')):
                out[str(path.relative_to(tree))] = render(env, path.read_text(), context)
            for name, task in calls(playbook):
                role_dir = role_dirs(tree, server_dir, name)
                if role_dir is None:
                    continue
                loop = task.get('loop') or task.get('with_items')
                loop_var = (task.get('loop_control') or {}).get('loop_var', 'item')
                if loop is None:
                    ctx = {**context, **resolved(env, task.get('vars') or {}, context)}
                    render_role(env, tree, role_dir, ctx, f'{server}:{name}: ', out)
                    continue
                items = loop if isinstance(loop, list) else _value(env, loop, context)
                for item in items if isinstance(items, list) else ():
                    ctx = {**context, loop_var: item}
                    ctx.update({k: _value(env, v, ctx) for k, v in (task.get('vars') or {}).items()})
                    label = item.get('name') if isinstance(item, dict) else item
                    render_role(env, tree, role_dir, ctx, f'{server}:{name}[{label}]: ', out)
    return out


def _value(env, expression, context):
    """A whole-expression Jinja value ("{{ users }}") as the object it names, not its text."""
    if isinstance(expression, str) and expression.strip().startswith('{{') and expression.strip().endswith('}}') \
            and expression.count('{{') == 1:
        try:
            return env.compile_expression(expression.strip()[2:-2], undefined_to_none=False)(**context)
        except Exception as e:  # noqa: BLE001
            return f'‹error: {type(e).__name__}: {e}›'
    return resolved(env, expression, context)


def tree_at(ref, into):
    """A ref's files, unpacked into a directory (git archive: no worktree to clean up after)."""
    archive = subprocess.run(['git', 'archive', '--format=tar', ref], capture_output=True, check=True).stdout
    with tempfile.TemporaryFile() as f:
        f.write(archive)
        f.seek(0)
        with tarfile.open(fileobj=f) as tar:
            tar.extractall(into, filter='data')
    return Path(into)


def is_error(text):
    return text.startswith('‹error:')


def compare(old, new):
    """(lines to print, new errors)."""
    changed = sorted(k for k in old.keys() & new.keys() if old[k] != new[k])
    added, removed = sorted(new.keys() - old.keys()), sorted(old.keys() - new.keys())
    same = len(old.keys() & new.keys()) - len(changed)
    errors = sorted(k for k in new if is_error(new[k]) and not is_error(old.get(k, '')))
    lines = [f'{same} identical, {len(changed)} changed, {len(added)} new, {len(removed)} gone'
             + (f', {len(errors)} newly failing to render' if errors else '') + '.']
    for k in errors:
        lines += ['', f'FAILS TO RENDER: {k}', f'  {new[k]}']
    for k in changed:
        lines += ['', f'--- {k}']
        lines += [l.rstrip('\n') for l in difflib.unified_diff(old[k].splitlines(True), new[k].splitlines(True),
                                                                 'before', 'after', n=2)][2:]
    for k in added:
        lines += ['', f'+++ new: {k}']
    for k in removed:
        lines += ['', f'--- gone: {k}']
    return lines, errors


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--base', default='origin/production', help='the ref to compare against')
    parser.add_argument('--head', help='a ref to render instead of this working tree')
    args = parser.parse_args(argv)
    root = Path(subprocess.run(['git', 'rev-parse', '--show-toplevel'], capture_output=True, text=True,
                               check=True).stdout.strip())
    os.chdir(root)
    with tempfile.TemporaryDirectory() as base_dir, tempfile.TemporaryDirectory() as head_dir:
        old = renders(tree_at(args.base, base_dir))
        new = renders(tree_at(args.head, head_dir) if args.head else root)
    lines, errors = compare(old, new)
    print(f'Rendered {args.base} against {args.head or "this tree"}: ' + lines[0])
    print('\n'.join(lines[1:]))
    return 1 if errors else 0


if __name__ == '__main__':
    sys.exit(main())
