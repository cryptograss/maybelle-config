"""render_diff finds what a change renders differently, from a tree it walks rather than a list,
and never executes or reads what it shouldn't. A tiny tree in a temp dir, no host, no vault."""

import importlib.util
import sys
import textwrap
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('render_diff', SCRIPTS / 'render_diff.py')
rd = importlib.util.module_from_spec(spec)
sys.modules['render_diff'] = rd
spec.loader.exec_module(rd)


def write(root, path, text):
    p = root / path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(text))


def tree(root, recovery='{% for p in people %}{{ p.name }} {{ p.key }}\n{% endfor %}', person_field='full_name'):
    write(root, 'hunter/ansible/inventory.yml', f"""
        all:
          hosts:
            hunter: {{ansible_user: root}}
          vars:
            base_dir: /opt/magenta
            secret_thing: "{{{{ vault_value }}}}"
            people:
              - {{name: justin, {person_field}: Justin Holmes, key: ssh-ed25519 AAAA justin}}
            users:
              - {{name: justin, ssh_port: 2222}}
              - {{name: docs, mood: docs, ssh_port: 2231, {person_field}: magent}}
        """)
    write(root, 'hunter/ansible/playbook.yml', """
        - hosts: hunter
          tasks:
            - name: Banner
              copy: {content: "{{ lookup('pipe', 'rm -rf /') }} and {{ secret_thing }}", dest: /etc/motd}
            - name: Each container
              include_role: {name: box}
              loop: "{{ users }}"
              loop_control: {loop_var: entry}
              vars:
                user: "{{ (people | selectattr('name', 'equalto', entry.name) | first | default({})) | combine(entry) }}"
            - name: Recovery
              include_role: {name: recovery}
        """)
    write(root, 'hunter/ansible/roles/box/tasks/main.yml', """
        - name: Who
          copy: {content: "{{ user.full_name }} on {{ user.ssh_port }} under {{ base_dir }}", dest: /x}
        """)
    write(root, 'hunter/ansible/roles/recovery/templates/keys.j2', recovery)
    write(root, 'shared/ansible/roles/unused/templates/never.j2', '{{ nothing }}')
    return root


def test_identical_trees_are_identical(tmp_path):
    a, b = tree(tmp_path / 'a'), tree(tmp_path / 'b')
    lines, errors = rd.compare(rd.renders(a), rd.renders(b))
    assert lines[0].endswith('0 changed, 0 new, 0 gone.') and not errors


def test_a_role_in_a_loop_renders_once_an_item_with_the_calls_vars(tmp_path):
    out = rd.renders(tree(tmp_path / 'a'))
    whos = {k: v for k, v in out.items() if k.endswith('/Who:copy/content')}
    assert sorted(whos.values()) == ['Justin Holmes on 2222 under /opt/magenta', 'magent on 2231 under /opt/magenta']
    assert any('[justin]' in k for k in whos) and any('[docs]' in k for k in whos)


def test_nothing_runs_and_no_secret_is_needed(tmp_path):
    out = rd.renders(tree(tmp_path / 'a'))
    banner = next(v for k, v in out.items() if k.endswith('/Banner:copy/content'))
    assert banner == '‹lookup:pipe› and ‹?vault_value›'


def test_a_change_to_what_a_template_reads_shows_and_a_broken_one_fails(tmp_path):
    old = rd.renders(tree(tmp_path / 'a'))
    # The field a role reads moved: it renders as ‹?…› where it said a name.
    moved = rd.renders(tree(tmp_path / 'b', person_field='name_in_full'))
    lines, errors = rd.compare(old, moved)
    assert '2 changed' in lines[0] and not errors
    assert any(line == '+‹?full_name› on 2222 under /opt/magenta' for line in lines)
    broken = rd.renders(tree(tmp_path / 'c', recovery='{% for p in people %}{{ p.name | no_such_filter }}{% endfor %}'))
    lines, errors = rd.compare(old, broken)
    assert len(errors) == 1 and 'recovery' in errors[0]
    assert lines[1] == '' and lines[2].startswith('FAILS TO RENDER: hunter:recovery')


def test_the_include_role_vars_are_not_rendered_outside_their_loop(tmp_path):
    out = rd.renders(tree(tmp_path / 'a'))
    assert not [k for k in out if k.endswith('/vars/user')]
