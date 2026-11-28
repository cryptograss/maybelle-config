# jenkins/jenkins:lts-jdk17 froze at 2.541.3 on 18 Mar 2026 when Jenkins LTS moved to Java 21 (re, #security, 15 Nov).
FROM jenkins/jenkins:lts-jdk21

USER root

# The libraries a node build wants (cairo, pango, librsvg), and the rest the builds need.
# Node itself comes below: the distro's is too old, as it is for rabbithole on the VPS.
RUN apt-get update && apt-get install -y \
    build-essential \
    libcairo2-dev \
    libpango1.0-dev \
    libjpeg-dev \
    libgif-dev \
    librsvg2-dev \
    rsync \
    sudo \
    curl \
    jq \
    docker.io \
    php-cli \
    php-xml \
    php-mbstring \
    php-intl \
    php-curl \
    unzip \
    msmtp \
    msmtp-mta \
    python3 \
    python3-pip \
    python3-venv \
    git \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Node (magent, #interfaces, 28 Nov 2026). This image never had it, under a comment that said it
# did: pickipedia's build skipped its JavaScript check ("node not installed; skipping
# check-verify-js.mjs"), and from 28 Nov wrote an empty assets/connectome/card-titles.json, so a
# shared /bacon link to a show said "The Oracle of Bluegrass Bacon" and not the show's own name.
# NodeSource 22, the version the VPS builds rabbithole with.
RUN curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key -o /usr/share/keyrings/nodesource.asc && \
    echo "deb [signed-by=/usr/share/keyrings/nodesource.asc] https://deb.nodesource.com/node_22.x nodistro main" \
        > /etc/apt/sources.list.d/nodesource.list && \
    apt-get update && apt-get install -y nodejs && \
    rm -rf /var/lib/apt/lists/* && \
    node --version

# The Blue Railroad import bot, from this repository: maybelle/blue-railroad-import/, which
# maybelle.yml copies into this build context. It used to be installed from GitHub, which meant
# Jenkins ran code that never passed githouse (Justin, #uploads-and-embeds, 23 Nov 2026).
COPY blue-railroad-import /opt/blue-railroad-import-src
RUN python3 -m venv /opt/blue-railroad-import && \
    /opt/blue-railroad-import/bin/pip install --no-cache-dir /opt/blue-railroad-import-src

# Install Composer
RUN curl -sS https://getcomposer.org/installer | php -- --install-dir=/usr/local/bin --filename=composer

USER jenkins

# Install required plugins
RUN jenkins-plugin-cli --plugins \
    configuration-as-code \
    workflow-aggregator \
    git \
    github \
    credentials-binding \
    pipeline-utility-steps \
    matrix-auth \
    job-dsl \
    workflow-job \
    pipeline-model-definition \
    pipeline-stage-view \
    pipeline-graph-view \
    dark-theme \
    github-branch-source \
    mask-passwords \
    build-name-setter \
    pipeline-github

# Create directory for Configuration as Code
RUN mkdir -p /var/jenkins_home/casc_configs \
    && java -XshowSettings:properties -version 2>&1 | awk -F'= ' '/java\.specification\.version =/{print "java:", $2; ok=($2+0 >= 21)} END{exit !ok}' \
    && ver=$(java -jar /usr/share/jenkins/jenkins.war --version) && echo "jenkins: $ver" \
    && awk -v v="$ver" 'BEGIN{split(v, a, "."); exit !(a[1] > 2 || (a[1] == 2 && a[2] >= 580))}'

# Skip initial setup wizard, and upgrade bundled .jpi plugins in JENKINS_HOME when a rebuild brings newer ones
ENV JAVA_OPTS="-Djenkins.install.runSetupWizard=false" \
    PLUGINS_FORCE_UPGRADE=true \
    TRY_UPGRADE_IF_NO_MARKER=true