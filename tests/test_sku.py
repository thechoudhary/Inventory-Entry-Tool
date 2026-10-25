import unittest
from sku import abbreviate, generate_sku, colour_code

class TestSKU(unittest.TestCase):
    def test_digits_preserved(self):
        self.assertIn("10X", abbreviate("Model 10X"))

    def test_custom_phrase(self):
        self.assertEqual(abbreviate("Washing Machine", [{"name":"Washing Machine","code":"WM","priority":10}]), "WM")

    def test_sku_order_is_category_brand_model_colour_grade(self):
        self.assertEqual(
            generate_sku("ELE", "WHP", "Item", "Washing Machine 10X", "G1", "BLURED",
                         [{"name":"Washing Machine","code":"WM","priority":10}]),
            "ELE-WHP-WM10X-BLURED-G1"
        )

    def test_colour_code_sorted_by_name(self):
        self.assertEqual(colour_code(["Red", "Blue"]), "BLUERED")

if __name__ == "__main__":
    unittest.main()
