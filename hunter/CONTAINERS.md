# What a container on hunter is

**Since 1 Nov 2026, Moods own directories and agents own processes**
(Cryptograss:Moods as Directories on PickiPedia; the model is
Cryptograss:Mood Architecture). A Mood is `/moods/<slug>`, seen by every
container; each agent has one container and one poller (`emergent-<name>`);
people have `workbench`. The sections at the end say how. The Mood containers
below (uploads-and-embeds, docs-tests-qa) were retired in step 5, their
workspaces folded into `/moods`; what follows about them is history.

## A container belongs to a Mood (until 1 Nov 2026)

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
  by it. They come from the vault as `<agent>_runner_key` and
  `<agent>_pickipedia_bot_password` (container_startup.py `setup_agent`).
- **the model vendors' keys, shared:** one `model_<vendor>_api_key` in the
  vault per vendor, for every agent; an agent is given its own
  (`<agent>_<vendor>_api_key`) only when it needs a separate limit or bill.
  Each agent's copy is still in its own home, readable only by it. Its
  poller tells memory-lane which vendors it can reach, never the keys, and
  the Mood's model menu greys out the rest. What each agent spends is in the
  record, turn by turn, so separate keys aren't needed to tell them apart.
- **its own harness and poller.** magent runs on Claude Code. re runs on Pi
  (memory-lane `poller/pi_harness.py`), which posts memory-lane's own record
  events, so nothing about Claude Code's files is involved. With an
  `also_harness`, a Mood's model chooses between the two (magenta#71):
  Anthropic's models run on Claude Code, on the subscription, which Anthropic
  refuses to Pi; other vendors' on Pi. re has `also_harness: claude-code`, so
  its Claude Code turns are filed as re's, by its key. Each agent's poller
  answers mentions of it in every Mood, with its own key; the container's Mood
  is its home, and it speaks up unasked only where it already has a session.
- **its own clones** of the repositories, in its own `~/workspace`.
- **its own home on hunter's disk,** `/opt/magenta/<container>/agents/<agent>`,
  mounted at `/home/<agent>` under the uid the inventory gives it (from 2000,
  so its files on hunter don't look like a host user's). Recreating the
  container keeps its harness's sessions, its poller's state and its clones,
  as magent's home always has been. The first deploy with an agent in the
  inventory makes the directory, carrying out a home the agent had been
  living in inside the container (`hunter/scripts/agent_homes.py`). An agent
  moving to another container names the one it leaves as `home_from`, and its
  home is moved over whole, once that container no longer mounts it.
- **its clones brought up to date as it starts:** first the repository its
  rules and letter are in, pulled before its poller starts; the rest after,
  in the background (`/tmp/clones-<agent>.log`), so a big clone (arthel, 13
  minutes) never keeps anyone from talking to it.

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

Its first container was `docs-tests-qa`, its home Mood. It moved to the
shared `agency`, from there to its own, `agency-re`, and then to the same
container renamed `emergent-re` (below), its home with it each time.

## emergent-<name>: each agent's own container

Agents answer in every Mood, so where their processes run isn't named after a
Mood (Justin, magent-self, 2026-10-21). Each agent has a container of its own,
`emergent-<name>` (magent-self, 23-24 Oct 2026: Justin, re, magent; first
called `agency-<name>`, renamed in emergence on 31 Oct 2026), holding its
home, its poller and its rules bundle, and nothing for people:

- **`kind: agent`:** no published ports, no code-server, no Godot workspace, no
  previews (`CONTAINER_KIND=agent`, container_startup.py). Recovery reaches it
  with `docker exec`.
- **Its own ceiling,** `memory_limit` and `cpus` in its entry: a runaway turn
  can't starve the rest of hunter, or another agent, and a bad start takes down
  one agent, not all of them. The shared `agency` before it could cap the
  agents together but not apart.
- **A Mood-kind entry** (`mood: emergent`) for what that brings: no docker.sock
  and full tools only for its `people`. No Mood called emergent need exist, and
  magent's own poller isn't started in it (`mood_poller: false`).
- **Its home** is `/opt/magenta/emergent-<name>/agents/<name>` on hunter's disk.

An agent's poller starts only once its runner key is in the vault; blu's is
held back until the poller passes its rules bundle as system prompt, since
blu's rules live only on PickiPedia.

**Retiring a container:** an entry taken out of the inventory goes in
`retired:`. The playbook runs `docker compose down` for it before making the
others, and renames its compose file `docker-compose.retired.yml`; everything
else under `/opt/magenta/<name>` stays on disk. That is also what lets an
agent's home move out of it (`home_from`): agent_homes.py refuses while a
compose file still mounts the home. The shared `agency` was the first retired
this way, when re and blu moved to `agency-re` and `agency-blu`; those two
were retired in turn when their homes moved to `emergent-re` and `emergent-blu`.

**Renaming a container:** give the entry its new name and `renamed_from: <old>`,
and put the old name in `retired:`. After the retire step, the user-instance
role moves `/opt/magenta/<old>` to `/opt/magenta/<new>` whole, the first time,
and refuses while the old compose file is still live. `delivery-kid` became
`uploads-and-embeds` this way on 1 Nov 2026, taking its Mood's name.

## /moods: a directory per Mood

Moods own directories; agents own processes (Justin, emergence, 1 Nov 2026;
the plan and its steps are on PickiPedia, Cryptograss:Moods as Directories).
`/opt/magenta/moods/<slug>` on hunter's disk holds a Mood's clones and files,
and every container mounts it at `/moods`:

- **The group `moods`, gid 3000,** owns it, here and in the image. magent is
  in it from the image; each agent joins at startup (`setup_agent`).
- **setgid and a default ACL** (playbook.yml) keep whatever anyone makes in it
  the group's to write, whatever their umask: one agent's clone is another's
  to commit in. Tried with magent and re as different users, umask 022: each
  committed in the other's clone and worktree, and `git gc` ran clean.
- **Git trusts `/moods/*`** (`safe.directory`, system-wide in the image), so
  a clone made by one user isn't "dubious ownership" to another.
- **A thread** gets a worktree under `/moods/<slug>/threads/<thread>/`.
- **The hourly backup** (`hunter-vcs-unpushed`) keeps what's only here in
  each Mood's directory too, as `moods/<slug>/...`.

## workbench: the people's box

One container for people (Moods as Directories, step 3): a terminal,
code-server and previews, with every Mood's directory at `/moods`, as in each
agent's container. No poller, no docker.sock (a Mood-kind entry, `mood:
workbench`). Its keys are every person's in its `people`.

- `ssh sshrouter@hunter chartifacts` is a shell in `/moods/chartifacts`;
  `... chartifacts tmux` a tmux session named for the Mood, started there and
  shared by whoever joins it. Only a slug whose directory exists counts;
  anything else is, as before, a command in your own container.
- `ssh sshrouter@hunter workbench` is a shell in its home.
- Everyone with a key is routed now: to their own container while they still
  have one, else to workbench.
- Its own port too, 2240, for scp, rsync and VS Code Remote; previews on
  `workbench0-9.hunter` until previews go by Mood (step 4).

## Previews by Mood: <slug>0-9.hunter

Each Mood with a directory under `/moods` has ten preview ports in
workbench, from 15000 up, given the first time it's seen and kept in its
`.previews` file (ports beside hostnames; renaming the directory keeps
them). A dev server started in workbench on one of them, bound to
`0.0.0.0`, is at `https://<slug><N>.hunter.cryptograss.live`.

- `hunter-mood-previews` (mood-previews role), each minute as root, writes
  `/etc/caddy/moods.caddy` (each hostname to workbench's address on
  magenta-net: no published ports) and `/etc/caddy/moods-ask`, and reloads
  Caddy only when they change and `caddy validate` takes them.
- Certificates on demand: one is asked for only when a hostname is first
  visited, and only for a hostname in `moods-ask` (Caddy's own
  `localhost:5555` answers). Ten names a Mood, all at once, would pass
  Let's Encrypt's 50 new certificates a week for cryptograss.live.
- A Mood named like a container that still has its own `<name>0-9`
  (uploads-and-embeds, docs-tests-qa) waits until that container retires.

## Step 5: the Mood containers fold into /moods

uploads-and-embeds (delivery-kid before 1 Nov) and docs-tests-qa are
`retired`. Their workspaces move, whole and once, into
`/opt/magenta/moods/<slug>` (`hunter/scripts/fold_mood_homes.py`, the
inventory's `folds`): clones, worktrees, uncommitted edits, branches on no
remote. A worktree records both its ends by absolute path, so each is
rewritten from `/home/magent/workspace/` to `/moods/<slug>/`, and the group
`moods` is given the lot. A `.folded-from` file in the Mood's directory says
what came from where. Tried on a copy of uploads-and-embeds' real
workspace: all 21 worktrees resolve from `/moods`, none prunable, and every
uncommitted edit came along.

Their magent sessions stay on their homes' disk, not resumed: with the Mood
containers gone, justin-arthel's poller is magent's only one
(`magent_only_poller`), so a mention there starts over from the record.


## emergent-magent: magent's own container

Step 6 of Moods as Directories: magent gets a container of its own, as re and
blu have, and its poller leaves justin-arthel.

- **Its home is the one it has always had.** `home_dir:
  /opt/magenta/justin/home` mounts it where it is, at `/home/magent`: 11G of
  Claude Code sessions, the clones and the poller's state, not copied, so
  every session resumes. While justin-arthel lives it mounts the same home,
  without a poller (`mood_poller: false`), and it writes the home's files
  (`.gitconfig`, `CLAUDE.md`, the arthel `.env`); an entry with `home_dir`
  writes none of them. When justin-arthel retires, the home moves to
  `/opt/magenta/emergent-magent/home` and `home_dir` goes.
- **No `mood:`.** magent answers every Mood, and keeps docker.sock (Justin,
  emergence, 1 Nov 2026, 05:24Z).
- `kind: agent`: no ports, no code-server, no previews. 8g and 3 CPUs.
  `ssh sshrouter@hunter emergent-magent` joins its tmux.
- **The watcher** doesn't read a home twice: an entry with `home_dir` is
  left out of its mounts.
