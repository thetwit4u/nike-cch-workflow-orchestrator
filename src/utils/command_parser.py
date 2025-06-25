# Based on the command type, we construct the body differently.
body = {}
if command_type == "ASYNC_REQ":
    body = {
        "capability_id": self.node_config.get("capability_id"),
        "context": self._extract_keys(self.node_config.get("input_keys")),
        "request_output_keys": self.node_config.get("output_keys")
    }

# Add other command type body structures here if needed 