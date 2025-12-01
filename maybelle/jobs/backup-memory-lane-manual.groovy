pipelineJob('backup-memory-lane-manual') {
    description('Check Memory Lane backup status on demand')
    definition {
        cps {
            script('''
                pipeline {
                    agent any

                    stages {
                        stage('Get current block height') {
                            steps {
                                script {
                                    def blockHeight = sh(
                                        script: 'curl -s https://eth.blockscout.com/api/v2/stats | jq -r .total_blocks',
                                        returnStdout: true
                                    ).trim()
                                    env.BLOCK_HEIGHT = blockHeight
                                    echo "Current Ethereum block: ${blockHeight}"
                                }
                            }
                        }

                        stage('Check backup status') {
                            steps {
                                sh """
                                    BACKUP_DIR="/mnt/persist/magenta/backups"

                                    echo "=== Memory Lane Backup Status ==="
                                    echo ""

                                    # Find most recent backup
                                    LATEST=\\$(ls -t "\\$BACKUP_DIR"/*.dump 2>/dev/null | head -1)
                                    if [ -z "\\$LATEST" ]; then
                                        echo "ERROR: No backup files found!"
                                        exit 1
                                    fi

                                    # Check age of most recent backup
                                    LATEST_AGE_HOURS=\\$(( (\\$(date +%s) - \\$(stat -c%Y "\\$LATEST")) / 3600 ))
                                    LATEST_SIZE=\\$(stat -c%s "\\$LATEST")
                                    LATEST_NAME=\\$(basename "\\$LATEST")

                                    echo "Latest backup: \\$LATEST_NAME"
                                    echo "Size: \\$(numfmt --to=iec \\$LATEST_SIZE)"
                                    echo "Age: \\$LATEST_AGE_HOURS hours"
                                    echo ""

                                    if [ \\$LATEST_AGE_HOURS -gt 25 ]; then
                                        echo "WARNING: Latest backup is more than 25 hours old!"
                                    else
                                        echo "OK: Backup is recent"
                                    fi

                                    echo ""
                                    echo "=== All Backups ==="
                                    ls -lht "\\$BACKUP_DIR"/*.dump 2>/dev/null

                                    echo ""
                                    BACKUP_COUNT=\\$(ls -1 "\\$BACKUP_DIR"/*.dump 2>/dev/null | wc -l)
                                    echo "Total backups: \\$BACKUP_COUNT"

                                    echo ""
                                    echo "=== Backup Log (last 20 entries) ==="
                                    if [ -f "\\$BACKUP_DIR/backup.log" ]; then
                                        tail -20 "\\$BACKUP_DIR/backup.log"
                                    else
                                        echo "(no backup log found)"
                                    fi
                                """
                            }
                        }
                    }
                }
            '''.stripIndent())
            sandbox()
        }
    }
}
