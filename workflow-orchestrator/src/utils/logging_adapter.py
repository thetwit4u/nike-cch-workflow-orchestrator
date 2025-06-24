import logging

class WorkflowIdAdapter(logging.LoggerAdapter):
    """
    A logger adapter to automatically inject the workflowInstanceId
    into all log messages.
    """
    def process(self, msg, kwargs):
        # If a workflow_id is present in the extra context, prepend it to the message.
        if 'workflow_id' in self.extra:
            return f"[WorkflowID: {self.extra['workflow_id']}] {msg}", kwargs
        return msg, kwargs 