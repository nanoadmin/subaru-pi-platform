FROM nodered/node-red:latest

# Pin dashboard nodes in the image so a fresh clone works without manual npm steps.
RUN npm install --omit=dev --no-fund --no-audit node-red-dashboard@3.6.6
