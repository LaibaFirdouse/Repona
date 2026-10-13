"""Eight baseline cases. Discovery, packet membership, and explanation are separate."""

from __future__ import annotations

FLASK = "https://github.com/pallets/flask"
ITSDANGEROUS = "https://github.com/pallets/itsdangerous"
HELLO = "https://github.com/octocat/Hello-World"

# Live repository questions. expected_lines are inclusive chunk lines from the indexed source.
LIVE = (
    {
        "id": "flask_exact",
        "repository": FLASK,
        "question": "In Flask, how does Flask.dispatch_request invoke the matched view function?",
        "expected": ("Flask.dispatch_request",),
        "lines": {"Flask.dispatch_request": (966, 990)},
    },
    {
        "id": "flask_dispatch",
        "repository": FLASK,
        "question": "How does Flask match an incoming request to a view and return the result?",
        "expected": ("Flask.dispatch_request",),
        "lines": {"Flask.dispatch_request": (966, 990)},
    },
    {
        "id": "flask_response_one",
        "repository": FLASK,
        "question": "How does Flask convert a view's return value into a response?",
        "expected": ("Flask.make_response",),
        "lines": {"Flask.make_response": (1224, 1364)},
    },
    {
        "id": "flask_response_many",
        "repository": FLASK,
        "question": "How does Flask process and finalize a response?",
        "expected": ("Flask.finalize_request", "Flask.process_response"),
        "lines": {
            "Flask.finalize_request": (1021, 1051),
            "Flask.process_response": (1394, 1418),
        },
    },
    {
        "id": "itsdangerous_exact",
        "repository": ITSDANGEROUS,
        "question": "How does Signer.sign produce a signed value?",
        "expected": ("Signer.sign",),
        "lines": {"Signer.sign": (222, 225)},
    },
    {
        "id": "itsdangerous_sign",
        "repository": ITSDANGEROUS,
        "question": "How does a value get signed and later checked?",
        "expected": ("Signer.sign", "Signer.unsign"),
        "lines": {
            "Signer.sign": (222, 225),
            "Signer.unsign": (244, 256),
        },
        # Both methods already reach the packet. This measures the explanation.
        "known_miss": False,
    },
    {
        "id": "hello_abstain",
        "repository": HELLO,
        "question": "Where is the request dispatcher?",
        "expected": (),
        "lines": {},
        "abstain": True,
    },
)

JS_SOURCE = """function build(name) {
  if (!name) {
    return prefix.concat("guest");
  }
  return prefix.concat(name);
}
"""

CAMEL_SYMBOLS = (
    {"name": "getSignature", "qualified_name": "Token.getSignature", "path": "src/token.ts", "id": "getSignature"},
    {"name": "verifyToken", "qualified_name": "Session.verifyToken", "path": "src/session.ts", "id": "verifyToken"},
    {"name": "makeResponse", "qualified_name": "App.makeResponse", "path": "src/app.ts", "id": "makeResponse"},
)

CAMEL = (
    {"question": "How does the type get a signature?", "expected": "Token.getSignature"},
    {"question": "How does the session verify a token?", "expected": "Session.verifyToken"},
    {"question": "How does the application make a response?", "expected": "App.makeResponse"},
)
