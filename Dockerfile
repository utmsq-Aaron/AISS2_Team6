# One image, many MCP servers. The SERVER env var selects which module to run,
# so every native FastMCP server (servers/*_mcp.py) shares this image and is
# started as its own container/service in docker-compose.
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Override per service in docker-compose (e.g. servers.routes_mcp)
ENV SERVER=servers.weather_mcp
CMD ["sh", "-c", "python -m $SERVER"]
