"""Questions that need different kinds of method selection.

Expected symbols come from the indexed source and from resolved edges.
An unresolved call is not an expected target. This module does not score fusion
and does not change how questions are answered.
"""

from __future__ import annotations

from app.evaluation.baseline import FLASK, HELLO, ITSDANGEROUS

SELECTION = (
    {
        "id": "signature_generation",
        "kind": "single_method",
        "repository": ITSDANGEROUS,
        "question": "How does a signature get generated?",
        "expected": ("Signer.get_signature",),
        "unsupported": (),
        "lines": {"Signer.get_signature": (215, 220)},
        "abstain": False,
        "note": "The implementation is Signer.get_signature. derive_key is a resolved callee; algorithm.get_signature is not.",
    },
    {
        "id": "signing_paraphrase",
        "kind": "multi_method",
        "repository": ITSDANGEROUS,
        "question": "How does a value get signed and later checked?",
        "expected": ("Signer.sign", "Signer.unsign", "Signer.validate"),
        "unsupported": (
            "TimestampSigner.validate",
            "SigningAlgorithm.verify_signature",
            "TestSigner.test_signer",
        ),
        "lines": {
            "Signer.sign": (222, 225),
            "Signer.unsign": (244, 256),
            "Signer.validate": (258, 266),
        },
        "abstain": False,
        "note": "Complementary Signer methods. Other classes and tests are not part of that explanation.",
    },
    {
        "id": "cross_file_flow",
        "kind": "cross_file",
        "repository": ITSDANGEROUS,
        "question": "Where does the payload go after Serializer.dumps returns?",
        "expected": (
            "Serializer.dumps",
            "Serializer.dump_payload",
            "Serializer.make_signer",
            "want_bytes",
        ),
        "unsupported": ("Signer.sign",),
        "lines": {
            "Serializer.dumps": (309, 320),
            "Serializer.dump_payload": (271, 276),
            "Serializer.make_signer": (278, 285),
            "want_bytes": (11, 17),
        },
        "abstain": False,
        "note": "Resolved callees are dump_payload, make_signer, and want_bytes. self.make_signer(salt).sign is unresolved, so Signer.sign is not a supported target.",
    },
    {
        "id": "caller_impact",
        "kind": "caller_impact",
        "repository": ITSDANGEROUS,
        "question": "What could be affected if Signer.sign changes?",
        "expected": (),
        "unsupported": ("Serializer.dumps", "TestSigner.test_signer"),
        "lines": {},
        "abstain": False,
        "note": "No resolved CALLS edge points at Signer.sign. Serializer.dumps and the tests mention sign, and those edges are unresolved.",
    },
    {
        "id": "dependency_usage",
        "kind": "dependency",
        "repository": FLASK,
        "question": "Where is the itsdangerous library used?",
        "expected": (),
        "expected_technology": "itsdangerous",
        "unsupported": (),
        "lines": {},
        "abstain": False,
        "note": "Flask records itsdangerous as a dependency. The import in src/flask/sessions.py is unresolved, so no in-repo symbol is a resolved usage target.",
    },
    {
        "id": "ambiguous_authentication",
        "kind": "ambiguous",
        "repository": FLASK,
        "question": "How does this repository handle authentication?",
        "expected": (),
        "unsupported": (),
        "lines": {},
        "abstain": True,
        "note": "A broad question should not be answered as one definitive method.",
    },
    {
        "id": "insufficient_payroll",
        "kind": "insufficient",
        "repository": FLASK,
        "question": "How does the repository calculate payroll tax withholding?",
        "expected": (),
        "unsupported": (),
        "lines": {},
        "abstain": True,
        "note": "That behavior is not in the indexed Flask source.",
    },
    {
        "id": "insufficient_hello",
        "kind": "insufficient",
        "repository": HELLO,
        "question": "Where is the request dispatcher?",
        "expected": (),
        "unsupported": (),
        "lines": {},
        "abstain": True,
        "note": "The indexed tree has no implementation to explain.",
    },
    {
        "id": "cue_dispatch",
        "kind": "single_method",
        "repository": FLASK,
        "question": "How does Flask match an incoming request to a view and return the result?",
        "expected": ("Flask.dispatch_request",),
        "unsupported": (),
        "lines": {"Flask.dispatch_request": (966, 990)},
        "abstain": False,
        "note": "The current behavior cues select this method. The comparison runs the same question with those cues turned off.",
    },
)
