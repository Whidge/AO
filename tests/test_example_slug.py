import unittest

from examples.slug import slugify


class SlugifyTest(unittest.TestCase):
    def test_lowercase_ascii_words_and_digits(self):
        self.assertEqual(slugify("Hello World 123"), "hello-world-123")

    def test_repeated_separators_form_single_hyphens(self):
        self.assertEqual(slugify("One__Two...Three\t\nFour"), "one-two-three-four")

    def test_leading_and_trailing_punctuation(self):
        self.assertEqual(slugify("... Hello, World! --"), "hello-world")

    def test_empty_or_punctuation_only(self):
        for text in ("", "...!?_-", " \t\n"):
            with self.subTest(text=text):
                self.assertEqual(slugify(text), "")

    def test_non_ascii_characters_are_separators(self):
        for text, expected in (("Hello\u2014WORLD", "hello-world"),
                               ("A\u00e9B", "a-b"), ("\u0130\u212a\u00e9", "")):
            with self.subTest(text=text):
                self.assertEqual(slugify(text), expected)

    def test_non_string_input_raises_type_error(self):
        for value in (None, 123, 1.5, True, b"Hello", [], {}):
            with self.subTest(value=value):
                with self.assertRaises(TypeError):
                    slugify(value)

    def test_max_length_truncates_without_trailing_hyphen(self):
        for limit, expected in ((1, "h"), (4, "hell"), (5, "hello"),
                                (6, "hello"), (7, "hello-w"),
                                (11, "hello-world"), (20, "hello-world")):
            with self.subTest(limit=limit):
                result = slugify("... Hello__WORLD!", max_length=limit)
                self.assertEqual(result, expected)
                self.assertLessEqual(len(result), limit)
                self.assertFalse(result.endswith("-"))

    def test_max_length_accepts_positional_argument(self):
        self.assertEqual(slugify("Hello World", 7), "hello-w")

    def test_max_length_handles_empty_slugs(self):
        for text in ("", "...!?_-", "\u0130\u212a\u00e9"):
            with self.subTest(text=text):
                self.assertEqual(slugify(text, max_length=1), "")

    def test_none_max_length_preserves_default_behavior(self):
        self.assertEqual(slugify("Hello World", max_length=None), "hello-world")

    def test_invalid_max_length_raises_value_error(self):
        for value in (0, -1, True, False, 1.0, 1.5, "5", b"5", [], {}):
            for text in ("Hello World", ""):
                with self.subTest(value=value, text=text):
                    with self.assertRaises(ValueError):
                        slugify(text, max_length=value)


if __name__ == "__main__":
    unittest.main()
