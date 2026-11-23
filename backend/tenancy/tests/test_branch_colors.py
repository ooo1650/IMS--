"""Branch colours: automatic and distinct by default, choosable by an admin."""
from django.test import TestCase

from ims.testutils import World
from tenancy.models import BRANCH_COLORS, Branch, Organization, branch_colors


class BranchColors(TestCase):
    def setUp(self):
        self.w = World()

    def test_automatic_colours_are_distinct_and_stable(self):
        self.assertEqual(branch_colors(self.w.org.id), {self.w.a.id: "blue", self.w.b.id: "orange"})
        c = Branch.objects.create(org=self.w.org, name="C", code="C")
        self.assertEqual(branch_colors(self.w.org.id)[c.id], "purple")
        self.assertEqual(branch_colors(self.w.org.id)[self.w.a.id], "blue")  # adding a branch changes nobody else
        other = Organization.objects.create(name="O2", code="o2")
        d = Branch.objects.create(org=other, name="D", code="D")
        self.assertEqual(branch_colors(other.id)[d.id], "blue")  # each business starts its own set

    def test_a_chosen_colour_is_kept_and_skipped_by_the_others(self):
        self.w.b.color = "blue"
        self.w.b.save()
        self.assertEqual(branch_colors(self.w.org.id), {self.w.a.id: "orange", self.w.b.id: "blue"})

    def test_admin_sets_it_and_everyone_sees_it(self):
        adm = self.w.client(self.w.admin, self.w.a)
        r = adm.patch(f"/api/v1/branches/{self.w.a.id}/", {"color": "teal"}, format="json")
        self.assertEqual((r.status_code, r.data["color"], r.data["color_shown"]), (200, "teal", "teal"))
        self.assertEqual(adm.patch(f"/api/v1/branches/{self.w.a.id}/", {"color": "green"}, format="json").status_code, 400)
        me = self.w.client(self.w.cashier_a, self.w.a).get("/api/v1/auth/me").json()
        self.assertEqual([b["color"] for b in me["branches"]], ["teal"])
        listed = {b["id"]: b["color_shown"] for b in adm.get("/api/v1/branches/").json()}
        self.assertEqual(listed, {self.w.a.id: "teal", self.w.b.id: "blue"})
        self.assertEqual(adm.patch(f"/api/v1/branches/{self.w.a.id}/", {"color": ""}, format="json").data["color_shown"], "blue")
        self.assertEqual(len(BRANCH_COLORS), 9)
        mgr = self.w.client(self.w.manager_a, self.w.a)
        self.assertEqual(mgr.patch(f"/api/v1/branches/{self.w.a.id}/", {"color": "pink"}, format="json").status_code, 403)
