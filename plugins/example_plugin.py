"""
Example Plugin — Custom ATS Handler for Bamboo HR.

Place this file in the plugins/ directory. It will be automatically
loaded on startup. Implement the register(registry) function to
hook into the LinkedIn Lightning Applier.
"""


def register(registry):
    """Called automatically by PluginLoader."""

    # Register plugin metadata
    registry.register_plugin(
        name="example-bamboo-ats",
        version="1.0.0",
        author="Your Name",
        description="Custom ATS handler for Bamboo HR applications",
    )

    # Example: Register a custom ATS handler
    # registry.register_ats("bamboohr", BambooHRHandler)

    # Example: Register a lifecycle hook
    # registry.register_hook("post_apply", on_applied)

    # Example: Register a custom notification channel
    # registry.register_notifier("custom_webhook", send_webhook)

    # Example: Register a custom archetype
    # registry.register_archetype("blockchain_engineer", {
    #     "keywords": ["blockchain", "web3", "solidity", "smart contract"],
    #     "emphasis": "distributed systems, cryptography, DeFi",
    # })


# def on_applied(job_id="", title="", company="", **kwargs):
#     """Called after every successful application."""
#     print(f"Applied to {title} at {company}!")


# def send_webhook(message):
#     """Custom notification channel."""
#     import requests
#     requests.post("https://your-webhook.com/notify", json={"text": message})
#     return True
