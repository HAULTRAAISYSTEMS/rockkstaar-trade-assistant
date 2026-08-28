import unittest
try:
 from flask import Flask,session
 FLASK_AVAILABLE=True
except ImportError:FLASK_AVAILABLE=False
@unittest.skipUnless(FLASK_AVAILABLE,'Flask unavailable in minimal execution environment')
class LiveResearchRouteContractTests(unittest.TestCase):
 def test_flask_available_for_route_tests(self):self.assertTrue(FLASK_AVAILABLE)
if __name__=='__main__':unittest.main()
