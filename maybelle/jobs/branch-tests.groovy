// Branch tests (Cryptograss:Moods/ramp/Plan, section 11): every branch pushed to githouse, tested on the
// `branches` agent, its console public like every console here.
//
// One fixed pipeline, written here, never a branch's Jenkinsfile (re, 9 Nov): a Jenkinsfile chooses its own
// node and its Groovy runs on the controller, beside jenkins_home and every credential. This script only
// clones the commit githouse names, anonymously, and runs the repository's own `ci/test` on the agent, as a
// user of its own (`builder`) that can't read the agent's secret: branch code runs there and nowhere else. The agent holds no secrets and reaches only the internet,
// through a proxy that lets npm, PyPI and GitHub through (maybelle.yml, "branches agent").
//
// githouse triggers it after every push it forwards to GitHub (githouse.py, test_branch), as the reporter.
pipelineJob('branch-tests') {
    description('Every branch pushed to githouse, tested on the branches agent (Plan, section 11). A repository\'s tests are its own ci/test.')
    logRotator {
        numToKeep(300)
        daysToKeep(30)
    }
    parameters {
        stringParam('REPO', '', 'The repository, as githouse names it (magenta, maybelle-config, ...)')
        stringParam('BRANCH', '', 'The branch, as pushed (re/fix-foo)')
        stringParam('COMMIT', '', 'The commit to test: exactly this one, whatever the branch is now')
    }
    definition {
        cps {
            sandbox(true)
            script('''
                // Checked here, before anything runs: these go into a shell.
                def repo = params.REPO ?: ''
                def branch = params.BRANCH ?: ''
                def commit = params.COMMIT ?: ''
                if (!(repo ==~ /[a-z0-9][a-z0-9.-]{0,63}/) || !(branch ==~ /[A-Za-z0-9._-]+(\\/[A-Za-z0-9._-]+)*/)
                        || !(commit ==~ /[0-9a-f]{40}/)) {
                    error("REPO, BRANCH and COMMIT, well formed, please")
                }
                currentBuild.displayName = "${repo}:${branch} ${commit.take(8)}"
                node('branches') {
                    deleteDir()
                    stage('Clone') {
                        // Anonymously, through the proxy: our repositories are public, and the agent has no token.
                        sh """
                            git init -q .
                            git remote add origin https://github.com/cryptograss/${repo}.git
                            git fetch -q --depth 50 origin ${commit}
                            git checkout -q --detach ${commit}
                            git log -1 --format='%h %an: %s'
                        """
                    }
                    stage('Test') {
                        if (!fileExists('ci/test')) {
                            echo "${repo} has no ci/test yet: nothing to run. Its tests are its own to name."
                            currentBuild.result = 'NOT_BUILT'
                            return
                        }
                        // As `builder`, never as the agent's own user, which holds its secret (re, 9 Nov):
                        // run-ci is the one thing this agent's user may sudo (jenkins-branches/run-ci).
                        timeout(time: 30, unit: 'MINUTES') {
                            sh 'sudo -n /usr/local/bin/run-ci'
                        }
                    }
                }
            ''')
        }
    }
}
