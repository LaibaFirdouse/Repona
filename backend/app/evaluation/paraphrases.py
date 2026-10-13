"""Paraphrases that describe a behavior without naming the implementation."""

PARAPHRASES = (
    {
        "id": "dispatch_match",
        "behavior": "view_dispatch",
        "question": "How does Flask match an incoming HTTP request to a view function and return that view's result?",
        "expected": ("Flask.dispatch_request",),
    },
    {
        "id": "dispatch_short",
        "behavior": "view_dispatch",
        "question": "How does Flask match an incoming request to a view and return the result?",
        "expected": ("Flask.dispatch_request",),
    },
    {
        "id": "dispatch_choose",
        "behavior": "view_dispatch",
        "question": "When a request arrives, how does Flask choose which view runs and return what that view produces?",
        "expected": ("Flask.dispatch_request",),
    },
    {
        "id": "exception_raises",
        "behavior": "exception_handling",
        "question": "What does Flask do when a view raises an error?",
        "expected": ("Flask.handle_exception", "Flask.handle_user_exception"),
    },
    {
        "id": "exception_reported",
        "behavior": "exception_handling",
        "question": "How are errors raised by a view function reported?",
        "expected": ("Flask.handle_exception", "Flask.handle_user_exception"),
    },
    {
        "id": "exception_fails",
        "behavior": "exception_handling",
        "question": "What happens if a view fails while serving a request?",
        "expected": ("Flask.handle_exception", "Flask.handle_user_exception"),
    },
    {
        "id": "response_turn",
        "behavior": "response_creation",
        "question": "How does Flask turn a view's return value into an HTTP response?",
        "expected": ("Flask.make_response",),
    },
    {
        "id": "response_convert",
        "behavior": "response_creation",
        "question": "How is the value returned by a view converted into the response sent to the client?",
        "expected": ("Flask.make_response",),
    },
    {
        "id": "response_build",
        "behavior": "response_creation",
        "question": "How does Flask build the HTTP response from what a view returns?",
        "expected": ("Flask.make_response",),
    },
    {
        "id": "response_create",
        "behavior": "response_creation",
        "question": "How does Flask convert a view's return value into a response?",
        "expected": ("Flask.make_response",),
    },
    {
        "id": "response_after",
        "behavior": "response_processing",
        "question": "What happens to the response after the view returns?",
        "expected": ("Flask.process_response",),
    },
    {
        "id": "response_finalize",
        "behavior": "response_finalizing",
        "question": "How does Flask process and finalize a response?",
        "expected": ("Flask.process_response", "Flask.finalize_request"),
    },
    {
        "id": "preprocess_before",
        "behavior": "request_preprocessing",
        "question": "What does Flask do to a request before the view is called?",
        "expected": ("Flask.preprocess_request",),
    },
    {
        "id": "preprocess_prepare",
        "behavior": "request_preprocessing",
        "question": "How does Flask prepare a request before the view runs?",
        "expected": ("Flask.preprocess_request",),
    },
    {
        "id": "preprocess_setup",
        "behavior": "request_preprocessing",
        "question": "How does Flask run setup work before a view handles a request?",
        "expected": ("Flask.preprocess_request",),
    },
)
