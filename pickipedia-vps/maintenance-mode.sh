#!/bin/bash
#
# Toggle PickiPedia maintenance mode
#
# Usage:
#   ./maintenance-mode.sh on    # Enable maintenance page
#   ./maintenance-mode.sh off   # Restore normal operation
#

set -euo pipefail

PICKIPEDIA_HOST="5.78.112.39"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

case "${1:-}" in
    on)
        echo "Enabling maintenance mode..."

        # Copy maintenance page
        scp "$SCRIPT_DIR/maintenance.html" "root@$PICKIPEDIA_HOST:/var/www/pickipedia/"

        # Update Caddy to serve maintenance page
        ssh "root@$PICKIPEDIA_HOST" 'cat > /etc/caddy/Caddyfile << "EOF"
# PickiPedia - Maintenance Mode

pickipedia.xyz, www.pickipedia.xyz {
    root * /var/www/pickipedia

    # Serve maintenance page for everything
    rewrite * /maintenance.html
    file_server

    encode gzip
}

:8081 {
    respond /health "OK" 200
}
EOF

systemctl reload caddy'

        echo "✓ Maintenance mode enabled"
        echo "  https://pickipedia.xyz now shows maintenance page"
        ;;

    off)
        echo "Disabling maintenance mode..."

        # Restore normal Caddy config
        ssh "root@$PICKIPEDIA_HOST" 'cat > /etc/caddy/Caddyfile << "EOF"
# PickiPedia - SSL termination and reverse proxy

pickipedia.xyz, www.pickipedia.xyz {
    reverse_proxy 127.0.0.1:8080

    header {
        X-Content-Type-Options nosniff
        X-Frame-Options SAMEORIGIN
        Referrer-Policy strict-origin-when-cross-origin
        -Server
    }

    encode gzip zstd

    @www host www.pickipedia.xyz
    redir @www https://pickipedia.xyz{uri} permanent
}

:8081 {
    respond /health "OK" 200
}
EOF

systemctl reload caddy'

        echo "✓ Maintenance mode disabled"
        echo "  https://pickipedia.xyz now serves MediaWiki"
        ;;

    *)
        echo "Usage: $0 {on|off}"
        echo ""
        echo "  on   - Show maintenance page"
        echo "  off  - Restore normal MediaWiki operation"
        exit 1
        ;;
esac
