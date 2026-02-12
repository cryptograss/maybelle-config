FROM node:20-slim

# Install yt-dlp and ffmpeg (needed for some video processing)
RUN apt-get update && apt-get install -y \
    python3 \
    python3-pip \
    ffmpeg \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install yt-dlp
RUN curl -L https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp -o /usr/local/bin/yt-dlp \
    && chmod a+rx /usr/local/bin/yt-dlp

WORKDIR /app

# Install dependencies
COPY package.json ./
RUN npm install --production

# Copy application
COPY server.js auth.js wiki-update.js ./

# Create staging directory
RUN mkdir -p /staging

EXPOSE 3001

CMD ["node", "server.js"]
