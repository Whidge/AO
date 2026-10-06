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


if __name__ == "__main__":
    unittest.main()
