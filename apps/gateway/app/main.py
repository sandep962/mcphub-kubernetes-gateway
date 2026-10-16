from fastapi import FastAPI

app = FastAPI(
    title="MCPHub Gateway",
    description="Team MCP gateway for securely accessing registered MCP servers.",
    version="0.1.0",
)


@app.get("/health", tags=["Health"])
async def health_check():
    return {
        "status": "healthy",
        "service": "mcphub-gateway",
    }
