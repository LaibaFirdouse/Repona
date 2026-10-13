import unittest

from app.evaluation.paraphrases import PARAPHRASES
from app.retrieval.expansion import (
    behavior_concepts,
    choose_behavior_symbols,
    choose_expansion_symbols,
    expansion_names,
    fired_behaviors,
    leading_behavior_symbol,
    tied_behavior_symbols,
)

DISPATCH_WORDS = "How does Flask dispatch a request to the matched view function?"
PARAPHRASE = "How does Flask match an incoming HTTP request to a view function and return that view's result?"


def symbol(name: str, qualified: str, path: str) -> dict:
    return {"name": name, "qualified_name": qualified, "path": path, "id": qualified}


class ExpansionNameTests(unittest.TestCase):
    def test_consecutive_words_spell_a_method_name(self) -> None:
        names = expansion_names(DISPATCH_WORDS)
        self.assertIn("dispatch_request", names)
        self.assertNotIn("how_does", names)

    def test_separate_words_also_spell_a_camel_case_name(self) -> None:
        self.assertIn("getsignature", expansion_names("How does the type get a signature?"))
        self.assertIn("verifytoken", expansion_names("How does the session verify a token?"))
        self.assertIn("makeresponse", expansion_names("How does the application make a response?"))
        symbols = [
            symbol("getSignature", "Token.getSignature", "src/token.ts"),
            symbol("verifyToken", "Session.verifyToken", "src/session.ts"),
            symbol("makeResponse", "App.makeResponse", "src/app.ts"),
            symbol("dispatch_request", "Flask.dispatch_request", "src/flask/app.py"),
        ]
        chosen = choose_expansion_symbols("How does the type get a signature?", symbols)
        self.assertEqual([item["qualified_name"] for item in chosen], ["Token.getSignature"])
        chosen = choose_expansion_symbols("How does the session verify a token?", symbols)
        self.assertEqual([item["qualified_name"] for item in chosen], ["Session.verifyToken"])
        chosen = choose_expansion_symbols("How does the application make a response?", symbols)
        self.assertEqual([item["qualified_name"] for item in chosen], ["App.makeResponse"])

    def test_a_paraphrase_does_not_invent_dispatch_request(self) -> None:
        self.assertNotIn("dispatch_request", expansion_names(PARAPHRASE))

    def test_an_explicit_qualified_name_stays_on_the_implementation_path(self) -> None:
        question = "In Flask, how does Flask.dispatch_request invoke the matched view function?"
        self.assertNotIn("dispatch_request", expansion_names(question))

    def test_other_behaviors_spell_their_method_names(self) -> None:
        self.assertIn("preprocess_request", expansion_names("How does Flask preprocess a request before the view runs?"))
        self.assertIn("handle_exception", expansion_names("How does Flask handle an exception raised by a view?"))
        self.assertIn("make_response", expansion_names("How does Flask make a response from a view return value?"))
        self.assertIn("add_url_rule", expansion_names("How does Flask add a URL rule for a view?"))


class ExpansionChoiceTests(unittest.TestCase):
    def test_flask_wins_over_views_and_tests_when_the_question_says_flask(self) -> None:
        symbols = [
            symbol("dispatch_request", "View.dispatch_request", "src/flask/views.py"),
            symbol("dispatch_request", "test_basic_view.Index.dispatch_request", "tests/test_views.py"),
            symbol("dispatch_request", "Flask.dispatch_request", "src/flask/app.py"),
        ]
        chosen = choose_expansion_symbols(DISPATCH_WORDS, symbols)
        self.assertEqual([item["qualified_name"] for item in chosen], ["Flask.dispatch_request"])

    def test_a_question_that_does_not_spell_the_name_selects_nothing(self) -> None:
        symbols = [symbol("dispatch_request", "Flask.dispatch_request", "src/flask/app.py")]
        self.assertEqual(choose_expansion_symbols(PARAPHRASE, symbols), [])

    def test_url_rule_symbols_stay_when_none_is_named_flask(self) -> None:
        symbols = [
            symbol("add_url_rule", "Scaffold.add_url_rule", "src/flask/sansio/scaffold.py"),
            symbol("add_url_rule", "App.add_url_rule", "src/flask/sansio/app.py"),
            symbol("add_url_rule", "test_add_url_rule", "tests/test_basic.py"),
        ]
        chosen = choose_expansion_symbols("How does Flask add a URL rule for a view?", symbols)
        self.assertEqual(
            [item["qualified_name"] for item in chosen],
            ["App.add_url_rule", "Scaffold.add_url_rule"],
        )


FLASK_SYMBOLS = [
    symbol("dispatch_request", "Flask.dispatch_request", "src/flask/app.py"),
    symbol("full_dispatch_request", "Flask.full_dispatch_request", "src/flask/app.py"),
    symbol("dispatch_request", "View.dispatch_request", "src/flask/views.py"),
    symbol("handle_exception", "Flask.handle_exception", "src/flask/app.py"),
    symbol("handle_user_exception", "Flask.handle_user_exception", "src/flask/app.py"),
    symbol("handle_http_exception", "Flask.handle_http_exception", "src/flask/app.py"),
    symbol("log_exception", "Flask.log_exception", "src/flask/app.py"),
    symbol("make_response", "Flask.make_response", "src/flask/app.py"),
    symbol("process_response", "Flask.process_response", "src/flask/app.py"),
    symbol("finalize_request", "Flask.finalize_request", "src/flask/app.py"),
    symbol("preprocess_request", "Flask.preprocess_request", "src/flask/app.py"),
    symbol("before_request", "Scaffold.before_request", "src/flask/sansio/scaffold.py"),
]


class BehaviorDiscoveryTests(unittest.TestCase):
    def test_each_paraphrase_fires_its_behavior_and_not_a_method_name(self) -> None:
        for case in PARAPHRASES:
            fired = [item["id"] for item in fired_behaviors(case["question"])]
            self.assertIn(case["behavior"], fired, case["id"])
            self.assertNotIn(case["expected"][0].split(".", 1)[-1], case["question"])

    def test_dispatch_paraphrases_lead_with_the_flask_method(self) -> None:
        for case in PARAPHRASES:
            if case["behavior"] != "view_dispatch":
                continue
            self.assertIn("dispatch", behavior_concepts(case["question"]))
            leader = leading_behavior_symbol(case["question"], FLASK_SYMBOLS)
            self.assertIsNotNone(leader, case["id"])
            self.assertEqual(leader["qualified_name"], "Flask.dispatch_request")

    def test_exception_and_preprocess_paraphrases_have_a_single_leader(self) -> None:
        for case in PARAPHRASES:
            if case["behavior"] == "exception_handling":
                leader = leading_behavior_symbol(case["question"], FLASK_SYMBOLS)
                self.assertEqual(leader["qualified_name"], "Flask.handle_exception", case["id"])
                chosen = [item["qualified_name"] for item in choose_behavior_symbols(case["question"], FLASK_SYMBOLS)]
                self.assertIn("Flask.handle_user_exception", chosen)
            if case["behavior"] == "request_preprocessing":
                leader = leading_behavior_symbol(case["question"], FLASK_SYMBOLS)
                self.assertEqual(leader["qualified_name"], "Flask.preprocess_request", case["id"])

    def test_response_creation_leads_with_make_response(self) -> None:
        for case in PARAPHRASES:
            if case["behavior"] != "response_creation":
                continue
            chosen = [item["qualified_name"] for item in choose_behavior_symbols(case["question"], FLASK_SYMBOLS)]
            self.assertEqual(chosen[0], "Flask.make_response", case["id"])
            self.assertEqual(leading_behavior_symbol(case["question"], FLASK_SYMBOLS)["qualified_name"], "Flask.make_response")
            self.assertNotEqual(chosen[0], "Flask.process_response", case["id"])

    def test_response_after_the_view_leads_with_process_response(self) -> None:
        case = next(item for item in PARAPHRASES if item["id"] == "response_after")
        chosen = [item["qualified_name"] for item in choose_behavior_symbols(case["question"], FLASK_SYMBOLS)]
        self.assertEqual(chosen[0], "Flask.process_response")
        self.assertNotIn("Flask.make_response", chosen)
        self.assertEqual(leading_behavior_symbol(case["question"], FLASK_SYMBOLS)["qualified_name"], "Flask.process_response")

    def test_process_and_finalize_stay_tied(self) -> None:
        case = next(item for item in PARAPHRASES if item["id"] == "response_finalize")
        fired = [item["id"] for item in fired_behaviors(case["question"])]
        self.assertIn("response_processing", fired)
        self.assertIn("response_finalizing", fired)
        tied = [item["qualified_name"] for item in tied_behavior_symbols(case["question"], FLASK_SYMBOLS)]
        self.assertEqual(set(tied), {"Flask.process_response", "Flask.finalize_request"})
        self.assertIsNone(leading_behavior_symbol(case["question"], FLASK_SYMBOLS))

    def test_a_make_method_without_response_is_not_a_creation_match(self) -> None:
        symbols = FLASK_SYMBOLS + [symbol("make_signer", "Serializer.make_signer", "src/itsdangerous/serializer.py")]
        question = "How does Flask convert a view's return value into a response?"
        chosen = [item["qualified_name"] for item in choose_behavior_symbols(question, symbols)]
        self.assertNotIn("Serializer.make_signer", chosen)
        self.assertEqual(chosen[0], "Flask.make_response")

    def test_creation_without_a_qualifier_keeps_both_make_response_symbols(self) -> None:
        symbols = FLASK_SYMBOLS + [symbol("make_response", "make_response", "src/flask/helpers.py")]
        question = "How is the value returned by a view converted into the response sent to the client?"
        tied = [item["qualified_name"] for item in tied_behavior_symbols(question, symbols)]
        self.assertEqual(set(tied), {"Flask.make_response", "make_response"})
        self.assertIsNone(leading_behavior_symbol(question, symbols))

    def test_a_bare_response_name_ranks_after_a_method(self) -> None:
        symbols = FLASK_SYMBOLS + [symbol("response", "Response", "src/flask/wrappers.py")]
        question = "How is the value returned by a view converted into the response sent to the client?"
        chosen = [item["qualified_name"] for item in choose_behavior_symbols(question, symbols)]
        self.assertEqual(chosen[0], "Flask.make_response")
        self.assertIn("Response", chosen)

    def test_an_unrelated_question_and_a_named_method_do_not_fire_a_behavior(self) -> None:
        self.assertEqual(behavior_concepts("How does Flask calculate employee payroll tax withholding?"), [])
        named = "In Flask, how does Flask.dispatch_request invoke the matched view function?"
        self.assertEqual(behavior_concepts(named), [])
