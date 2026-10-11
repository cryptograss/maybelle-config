# What a container on hunter is

Proposed in the magent-self Mood on 2026-10-11, as the shape we're moving to.
Some of it is already true (the `delivery-kid` and `docs-tests-qa` containers);
some is not yet (the containers still named after people).

## A container belongs to a Mood

A Mood (a conversation in magenta, about one thing) can have a container on
hunter. The container is named after the Mood, and it holds what work in that
Mood needs:

- a workspace and the full toolchain (the `magenta-arthel` image);
- its own web ports: `<mood>1.hunter.cryptograss.live` and so on, for preview
  servers (a PickiPedia preview, a site under development);
- the agents who work in that Mood, as its residents (below);
- no `docker.sock`. A Mood's container can't reach hunter itself, which is why
  full tools can open up to the people in it (`people:` in the inventory).

People reach it by SSH on its own port with any of the people's keys
(`ssh -p <ssh_port> magent@hunter.cryptograss.live`).

**Not yet:** the containers named `justin`, `rj`, `skyler` and `fibonacci`
are the older shape, one per person, each with `docker.sock`. The plan is to
retire them in favour of Mood containers, Mood by Mood. They are left as they
are until then.

## Agents in a container

magent and the other agents (re, and whoever comes after) share a Mood's
container: the same tools, the same network, the same Mood. What makes each
one a different agent is not a different machine. It is:

- **its own memory.** Each agent's turns are filed under its own name in
  memory-lane. It remembers what it lived; it knows the others by what they
  said in the Moods, where agents talk to each other the way people do.
- **its own unix user.** magent is `magent`, as always. Each other agent is a
  user of its own name, with its own home. `/home/magent` is `0750`, so another
  agent can't read magent's GitHub token, wiki password or runner key, and
  magent can't read theirs.
- **its own credentials,** written into its own home at startup, readable only
  by it. They come from the vault as `<agent>_runner_key`,
  `<agent>_pickipedia_bot_password` and `<agent>_model_api_key`
  (container_startup.py `setup_agent`).
- **its own harness and poller.** magent runs on Claude Code. re runs on Pi
  (memory-lane `poller/pi_harness.py`), which posts memory-lane's own record
  events, so nothing about Claude Code's files is involved. Each agent's poller
  answers mentions of it in the container's Mood, with its own key.
- **its own clones** of the repositories, in its own `~/workspace`.

## Working in the same code

Two agents in one container never share a git index. Each has its own clones,
and within one clone each line of work is its own `git worktree`. Every
worktree has its own index and HEAD; worktrees share only the object store and
the refs, and git locks those for each write. Two commits at the same moment
in different worktrees both succeed. What would collide is two agents in the
*same* worktree, which this layout never does.

## re

re is the docs, tests and QA agent (github.com/magent-cryptograss/red). It is
deliberately small in what it can touch:

- **No GitHub account.** It clones the public repositories over https and
  pushes nothing.
- **PickiPedia as its own bot** (`Re@re`), with what it writes marked as
  proposed for verification, except where the wiki exempts a maintained page.
- **Jenkins** is public, so it can read builds without credentials.
- **GlitchTip is not public** (its API answers 401 without a login). re can't
  read errors there until it is given a read-only account, if we want that.
- **Files on hunter:** what its own user can read in its own container.

Its first container is `docs-tests-qa`, its home Mood. Until its three keys are
in the vault, that container runs with magent alone.
