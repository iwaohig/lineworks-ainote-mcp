# Container image for catalog checks (e.g. Glama) and for running the server
# where Python is not installed. The server starts without credentials and
# answers MCP introspection (initialize / tools/list); tool calls need the
# OAuth login done once with `lineworks-ainote-mcp auth`, which stores tokens
# in the OS keychain, so real use is expected on a desktop, not in a container.
FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md LICENSE server.json ainote_mcp.py ./
RUN pip install --no-cache-dir .

ENTRYPOINT ["lineworks-ainote-mcp"]
