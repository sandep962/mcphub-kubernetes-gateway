
import argparse
import asyncio

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


async def main(create_product: bool, test_order_status: bool) -> bool:
    async with streamablehttp_client("http://127.0.0.1:8001/mcp") as (
        read_stream,
        write_stream,
        _,
    ):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()

            tools = await session.list_tools()
            print("\nAvailable MCP tools:")
            for tool in tools.tools:
                print(f"- {tool.name}: {tool.description}")

            for tool_name, arguments in [
                ("list_products", {"per_page": 10}),
                ("list_orders", {"per_page": 10}),
                ("get_order", {"order_id": 16}),
            ]:
                print(f"\nTesting {tool_name}...")
                result = await session.call_tool(tool_name, arguments)
                for item in result.content:
                    if hasattr(item, "text"):
                        print(item.text)
                if result.isError:
                    print("Tool returned an MCP error.")
                    return False

            if create_product:
                print("\nTesting create_product...")
                result = await session.call_tool(
                    "create_product",
                    {
                        "name": "MCPHub MCP Test Product",
                        "regular_price": "1.00",
                        "description": "Created by the MCPHub MCP verification client.",
                    },
                )
                for item in result.content:
                    if hasattr(item, "text"):
                        print(item.text)
                if result.isError:
                    print("Tool returned an MCP error.")
                    return False

            if test_order_status:
                print("\nTesting update_order_status: order 16 -> on-hold")
                result = await session.call_tool(
                    "update_order_status",
                    {"order_id": 16, "status": "on-hold"},
                )
                for item in result.content:
                    if hasattr(item, "text"):
                        print(item.text)
                if result.isError:
                    print("Status update failed; inspect the error before proceeding.")
                    return False

                print("\nVerifying order 16 status...")
                result = await session.call_tool(
                    "get_order", {"order_id": 16}
                )
                for item in result.content:
                    if hasattr(item, "text"):
                        print(item.text)
                if result.isError:
                    print("Could not verify the updated status.")
                    return False

                print(
                    "\nIf the returned status is on-hold, "
                    "restore order 16 to processing in the next step."
                )

            return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Verify the WooCommerce MCP server"
    )
    parser.add_argument(
        "--create-product",
        action="store_true",
        help="Create a test product after read tests pass",
    )
    parser.add_argument(
        "--test-order-status",
        action="store_true",
        help="Change order 16 to on-hold for a status-update test",
    )
    args = parser.parse_args()
    raise SystemExit(
        0
        if asyncio.run(main(args.create_product, args.test_order_status))
        else 1
    )
