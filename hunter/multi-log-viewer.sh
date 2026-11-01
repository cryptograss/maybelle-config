#!/bin/bash
# Multi-pane log viewer for hunter deployment
# Shows the running containers' logs in tmux panes, plus Caddy on the host
#
# Usage:
#   - Switch panes: Ctrl-b arrow-keys
#   - Copy from pane: Ctrl-b [ (enter copy mode), Space (start select), Enter (copy)
#   - Paste: Ctrl-b ]
#   - Detach: Ctrl-b d
#   - Reattach: tmux attach -t hunter-logs

SESSION="hunter-logs"

# Check if session already exists
tmux has-session -t $SESSION 2>/dev/null

if [ $? != 0 ]; then
    # Create new session with 4 panes
    tmux new-session -d -s $SESSION -n logs

    # Split into 4 panes
    tmux split-window -h -t $SESSION
    tmux split-window -v -t $SESSION:0.0
    tmux split-window -v -t $SESSION:0.2

    # Pane 0 (top-left): magent's container (its home and poller)
    tmux send-keys -t $SESSION:0.0 'ssh root@hunter.cryptograss.live "docker logs -f justin-arthel"' C-m

    # Pane 1 (top-right): workbench, the people's box
    tmux send-keys -t $SESSION:0.1 'ssh root@hunter.cryptograss.live "docker logs -f workbench-arthel"' C-m

    # Pane 2 (bottom-left): Caddy on the host (previews, <slug><N>.hunter)
    tmux send-keys -t $SESSION:0.2 'ssh root@hunter.cryptograss.live "journalctl -u caddy -f"' C-m

    # Pane 3 (bottom-right): the emergents' containers (emergent-*)
    tmux send-keys -t $SESSION:0.3 'ssh -t root@hunter.cryptograss.live "watch -n5 docker ps"' C-m

    # Set pane titles
    tmux select-pane -t $SESSION:0.0 -T "justin-arthel (magent)"
    tmux select-pane -t $SESSION:0.1 -T "workbench"
    tmux select-pane -t $SESSION:0.2 -T "Caddy (host)"
    tmux select-pane -t $SESSION:0.3 -T "all containers"

    # Enable pane border titles (show at top of each pane)
    tmux set-option -t $SESSION pane-border-status top
    tmux set-option -t $SESSION pane-border-format " #{pane_title} "
fi

# Attach to session
tmux attach-session -t $SESSION
