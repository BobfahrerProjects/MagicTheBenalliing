"""Import, proxies, valuation, identity, decks and the ManaBox round trip."""
from __future__ import annotations

import csv
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import helpers  # noqa: E402
from helpers import BIRD, SOL_A, SOL_B, STUDY, Sandbox, row  # noqa: E402

from mtgtool import (decks, identity, importer, manabox,  # noqa: E402
                     paths, value)


class TestParsing(unittest.TestCase):

    def test_duplicate_rows_aggregate_without_losing_cards(self):
        with Sandbox() as box:
            parsed = manabox.parse(box.csv())
            self.assertEqual(parsed.csv_rows, 8)
            self.assertEqual(len(parsed.lots), 7, "the two Squirrel rows must merge")
            self.assertEqual(parsed.lot_cards, parsed.csv_cards)

    def test_negative_control_a_stub_parser_would_fail(self):
        """If aggregation dropped the duplicate instead of summing, cards would be lost."""
        with Sandbox() as box:
            parsed = manabox.parse(box.csv())
            squirrel = [l for l in parsed.lots.values()
                        if l.scryfall_id == BIRD and not l.is_wishlist][0]
            self.assertEqual(squirrel.quantity, 2)
            self.assertEqual(squirrel.source_rows, 2)

    def test_missing_column_is_refused(self):
        with Sandbox() as box:
            path = os.path.join(box.dir, "bad.csv")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("Name,Quantity\nSol Ring,1\n")
            with self.assertRaises(manabox.ManaBoxFormatError):
                manabox.parse(path)

    def test_row_without_scryfall_id_is_refused(self):
        """Identification is impossible without an ID; never fall back to the name."""
        with Sandbox() as box:
            rows = helpers.default_rows()
            rows[0]["Scryfall ID"] = ""
            with self.assertRaises(manabox.ManaBoxFormatError):
                manabox.parse(box.csv(rows))


class TestProxies(unittest.TestCase):

    def test_all_four_markers_agreeing_means_proxy(self):
        with Sandbox() as box:
            parsed = manabox.parse(box.csv())
            self.assertEqual(parsed.conflicts, [])
            proxies = [l for k, l in parsed.lots.items()
                       if parsed.proxy_flags[k] and not l.is_wishlist]
            self.assertEqual(sum(l.quantity for l in proxies), 2)

    def test_disagreeing_markers_block_the_import(self):
        """A changed filing habit must become a question, not a wrong valuation."""
        with Sandbox() as box:
            rows = helpers.default_rows()
            # In the Proxies binder but priced and near mint: genuinely ambiguous.
            rows.append(row("Proxies", "binder", SOL_A, "Sol Ring", "40k", "252", 1,
                            price="2.00", condition="near_mint", misprint="false"))
            parsed = manabox.parse(box.csv(rows))
            self.assertEqual(len(parsed.conflicts), 1)
            self.assertIn("Proxies binder", parsed.conflicts[0].describe())

            conn = box.conn()
            with self.assertRaises(importer.ImportBlocked) as caught:
                importer.run_import(conn, box.csv(rows), offline=True, log=lambda *a: None)
            self.assertIn("proxies", str(caught.exception).lower())

    def test_an_override_settles_a_conflict(self):
        with Sandbox() as box:
            rows = helpers.default_rows()
            rows.append(row("Proxies", "binder", SOL_A, "Sol Ring", "40k", "252", 1,
                            price="2.00", condition="near_mint", misprint="false"))
            path = box.csv(rows)
            parsed = manabox.parse(path)
            key = parsed.conflicts[0].lot.key
            import json
            with open(paths.OVERRIDES, "w", encoding="utf-8") as fh:
                json.dump({"proxy": {manabox.key_str(key): True}}, fh)

            settled = manabox.parse(path)
            self.assertEqual(settled.conflicts, [])
            self.assertTrue(settled.proxy_flags[key])


class TestValuation(unittest.TestCase):

    def setUp(self):
        self.box = Sandbox().__enter__()
        self.conn = self.box.conn()
        importer.run_import(self.conn, self.box.csv(), offline=True, log=lambda *a: None)

    def tearDown(self):
        self.box.__exit__(None, None, None)

    def test_proxies_are_excluded_from_the_total(self):
        result = value.value_collection(self.conn)
        # Sol Ring 2.00 + Sol Ring 1.50 + 2x Rhystic Study 30.00
        #   + 2x Squirrel priced from USD (4.00 * 0.92) = 70.86
        self.assertAlmostEqual(result.real.value, 70.86, places=2)
        # The two proxied Rhystic Studies are worth 60.00 and must not be counted.
        self.assertAlmostEqual(result.proxy.value, 60.00, places=2)

    def test_negative_control_proxies_really_would_change_the_total(self):
        """Guards the test above: if proxies leaked in, the total would move a lot."""
        result = value.value_collection(self.conn)
        self.assertNotAlmostEqual(result.real.value,
                                  result.real.value + result.proxy.value, places=2)

    def test_wishlist_cards_are_never_owned(self):
        result = value.value_collection(self.conn)
        self.assertEqual(result.wishlist.cards, 3)
        self.assertNotIn("Wish List", result.by_binder)

    def test_usd_fallback_is_counted_and_reported(self):
        result = value.value_collection(self.conn)
        # The Squirrel has no EUR price, only USD.
        self.assertEqual(result.real.converted_cards, 2)
        self.assertIn("priced from USD", value.format_report(result))


class TestIdentity(unittest.TestCase):

    def setUp(self):
        self.box = Sandbox().__enter__()
        self.conn = self.box.conn()
        importer.run_import(self.conn, self.box.csv(), offline=True, log=lambda *a: None)

    def tearDown(self):
        self.box.__exit__(None, None, None)

    def test_an_ambiguous_name_is_refused(self):
        with self.assertRaises(identity.AmbiguousCard) as caught:
            identity.resolve(self.conn, "Sol Ring")
        self.assertEqual(len(caught.exception.candidates), 2)
        self.assertIn("40K", str(caught.exception))
        self.assertIn("BLC", str(caught.exception))

    def test_an_unambiguous_name_resolves(self):
        found = identity.resolve(self.conn, "Rhystic Study")
        self.assertEqual(found.scryfall_id, STUDY)

    def test_case_does_not_matter(self):
        self.assertEqual(identity.resolve(self.conn, "rhystic study").scryfall_id, STUDY)

    def test_unknown_name_is_refused_with_suggestions(self):
        with self.assertRaises(identity.UnknownCard) as caught:
            identity.resolve(self.conn, "Rystic Study")
        self.assertIn("Rhystic Study", caught.exception.suggestions)

    def test_a_name_id_mismatch_is_detected(self):
        self.assertIsNone(identity.verify_name(self.conn, STUDY, "Rhystic Study"))
        self.assertIsNotNone(identity.verify_name(self.conn, STUDY, "Black Lotus"))

    def test_ownership_is_counted_at_oracle_level_excluding_proxies(self):
        sol = identity.by_id(self.conn, SOL_A)
        self.assertEqual(identity.owned_copies(self.conn, sol.oracle_id), 2)
        study = identity.by_id(self.conn, STUDY)
        self.assertEqual(identity.owned_copies(self.conn, study.oracle_id), 2)
        self.assertEqual(identity.proxy_copies(self.conn, study.oracle_id), 2)

    def test_wishlist_cards_are_not_owned(self):
        bird = identity.by_id(self.conn, BIRD)
        self.assertEqual(identity.owned_copies(self.conn, bird.oracle_id), 2,
                         "2 real in Pool - Old; the 3 wishlist copies do not count")


class TestDecks(unittest.TestCase):

    def setUp(self):
        self.box = Sandbox().__enter__()
        self.conn = self.box.conn()
        importer.run_import(self.conn, self.box.csv(), offline=True, log=lambda *a: None)
        decks.seed_from_snapshot(self.conn)

    def tearDown(self):
        self.box.__exit__(None, None, None)

    def test_decks_are_seeded_from_manabox(self):
        names = sorted(d.name for d in decks.load_all())
        self.assertEqual(names, ["Hazel", "Nata", "Rootha"])

    def test_a_third_claim_on_two_copies_is_contested(self):
        """The one-card-one-deck rule. Two real Rhystic Studies, already both claimed."""
        self.assertEqual(decks.contentions(self.conn), [])

        third = decks.create("Test Deck")
        card = identity.resolve(self.conn, "Rhystic Study")
        added = decks.add_card(self.conn, third, card)
        self.assertEqual(added.status, "contested")

        found = decks.contentions(self.conn)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].name, "Rhystic Study")
        self.assertEqual(found[0].owned, 2)
        self.assertEqual(found[0].demanded, 3)
        self.assertEqual(found[0].shortfall, 1)

    def test_contention_report_offers_the_proxy_you_already_own(self):
        third = decks.create("Test Deck")
        decks.add_card(self.conn, third, identity.resolve(self.conn, "Rhystic Study"))
        report = decks.format_contentions(decks.contentions(self.conn))
        self.assertIn("proxies", report)
        self.assertIn("move it between decks", report)

    def test_a_proxy_slot_does_not_consume_a_real_copy(self):
        third = decks.create("Test Deck")
        card = identity.resolve(self.conn, "Rhystic Study")
        decks.add_card(self.conn, third, card, status="proxy")
        self.assertEqual(decks.contentions(self.conn), [])

    def test_drift_is_reported_when_the_plan_disagrees_with_manabox(self):
        self.assertEqual(decks.drift(self.conn), [])

        rootha = decks.load("rootha")
        sol = identity.by_id(self.conn, SOL_A)  # physically in Pool - Old
        decks.add_card(self.conn, rootha, sol, status="assigned")

        found = decks.drift(self.conn)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].card_name, "Sol Ring")
        self.assertIn("Pool - Old", found[0].actual)

    def test_removing_a_card_releases_the_claim(self):
        third = decks.create("Test Deck")
        card = identity.resolve(self.conn, "Rhystic Study")
        decks.add_card(self.conn, third, card)
        self.assertEqual(len(decks.contentions(self.conn)), 1)
        third = decks.load(third.deck_id)
        self.assertTrue(decks.remove_card(third, card.scryfall_id))
        self.assertEqual(decks.contentions(self.conn), [])


class TestManaBoxExport(unittest.TestCase):

    def setUp(self):
        self.box = Sandbox().__enter__()
        self.conn = self.box.conn()
        importer.run_import(self.conn, self.box.csv(), offline=True, log=lambda *a: None)
        decks.seed_from_snapshot(self.conn)

    def tearDown(self):
        self.box.__exit__(None, None, None)

    def test_export_uses_the_exact_manabox_columns(self):
        path = decks.export_manabox(self.conn, decks.load("rootha"))
        with open(path, encoding="utf-8", newline="") as fh:
            header = next(csv.reader(fh))
        self.assertEqual(header, manabox.EXPECTED_COLUMNS)

    def test_exported_deck_can_be_parsed_back_in(self):
        """Round trip: what we write, our own reader must accept."""
        path = decks.export_manabox(self.conn, decks.load("rootha"))
        parsed = manabox.parse(path)
        self.assertEqual(parsed.csv_cards, 1)
        lot = list(parsed.lots.values())[0]
        self.assertEqual(lot.binder_type, "deck")
        self.assertEqual(lot.binder_name, "Rootha")
        self.assertEqual(lot.scryfall_id, STUDY)

    def test_a_proxy_slot_exports_with_the_proxy_markers(self):
        deck = decks.create("Proxy Deck")
        decks.add_card(self.conn, deck, identity.resolve(self.conn, "Rhystic Study"),
                       status="proxy")
        path = decks.export_manabox(self.conn, deck)
        parsed = manabox.parse(path)
        key = list(parsed.lots)[0]
        self.assertTrue(parsed.proxy_flags[key],
                        "a proxy must re-import as a proxy, not as a real card")

    def test_wanted_cards_are_not_exported(self):
        deck = decks.create("Wanted Deck")
        bird = identity.by_id(self.conn, BIRD)
        decks.add_card(self.conn, deck, bird, status="wanted")
        path = decks.export_manabox(self.conn, deck)
        parsed = manabox.parse(path)
        self.assertEqual(parsed.csv_rows, 0)


class TestReimport(unittest.TestCase):

    def test_importing_the_same_export_twice_reports_no_change(self):
        """The second attempt is where bugs live, so the test repeats the operation."""
        with Sandbox() as box:
            conn = box.conn()
            path = box.csv()
            first = importer.run_import(conn, path, offline=True, log=lambda *a: None)
            second = importer.run_import(conn, path, offline=True, log=lambda *a: None)
            self.assertEqual(first["snapshot_id"], second["snapshot_id"])
            self.assertEqual(second["changes"], [])
            self.assertEqual(second["cards"], first["cards"])

    def test_a_later_export_produces_a_delta_against_the_earlier_one(self):
        with Sandbox() as box:
            conn = box.conn()
            importer.run_import(conn, box.csv(name="ManaBox_20260101.csv"),
                                offline=True, log=lambda *a: None)

            rows = helpers.default_rows()
            # Sol Ring moves from the pool into the Hazel deck.
            rows[0] = row("Hazel", "deck", SOL_A, "Sol Ring", "40k", "252", 1)
            second = importer.run_import(
                conn, box.csv(rows, name="ManaBox_20260202.csv"),
                offline=True, log=lambda *a: None)

            kinds = importer.summarise(second["changes"])
            self.assertEqual(kinds, {"moved": 1})


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestRebuild(unittest.TestCase):
    """The database must be a derived cache, not the only copy of anything."""

    TABLES = ["snapshots", "lots", "cards", "card_faces", "art_tags", "art_notes",
              "decks", "deck_slots", "assignments"]

    def _fingerprint(self, conn):
        import hashlib
        digest = hashlib.sha256()
        for table in self.TABLES:
            columns = [r[1] for r in conn.execute("PRAGMA table_info({})".format(table))]
            # imported_at is a log of when the import ran, not collection data.
            keep = [c for c in columns if c != "imported_at"]
            rows = conn.execute("SELECT {} FROM {} ORDER BY 1,2".format(
                ",".join(keep), table)).fetchall()
            for row in rows:
                digest.update(repr(tuple(row)).encode())
        return digest.hexdigest()

    def test_rebuilding_from_text_reproduces_the_database(self):
        with Sandbox() as box:
            conn = box.conn()
            importer.run_import(conn, box.csv(), offline=True, log=lambda *a: None)
            decks.seed_from_snapshot(conn)
            decks.sync_to_db(conn)
            before = self._fingerprint(conn)
            conn.close()

            os.remove(paths.DB)
            fresh = box.conn()
            for name in sorted(os.listdir(paths.SNAPSHOTS)):
                importer.run_import(fresh, os.path.join(paths.SNAPSHOTS, name),
                                    offline=True, log=lambda *a: None)
            decks.sync_to_db(fresh)
            self.assertEqual(self._fingerprint(fresh), before)

    def test_negative_control_the_fingerprint_notices_a_change(self):
        """Guards the test above: it must not be comparing nothing to nothing."""
        with Sandbox() as box:
            conn = box.conn()
            importer.run_import(conn, box.csv(), offline=True, log=lambda *a: None)
            before = self._fingerprint(conn)
            conn.execute("DELETE FROM lots WHERE rowid IN (SELECT rowid FROM lots LIMIT 1)")
            self.assertNotEqual(self._fingerprint(conn), before)


class TestStaging(unittest.TestCase):
    """ManaBox's download is always named ManaBox_Collection.csv, with no date."""

    def test_reimporting_the_identical_file_reuses_the_snapshot(self):
        with Sandbox() as box:
            path = box.csv(name="ManaBox_Collection.csv")
            first, date_a = importer.stage_snapshot(path)
            second, date_b = importer.stage_snapshot(path)
            self.assertEqual(first, second)
            self.assertEqual(date_a, date_b)
            staged = os.listdir(paths.SNAPSHOTS)
            self.assertEqual(len(staged), 1)

    def test_a_second_export_the_same_day_gets_its_own_snapshot(self):
        """Scan packs, export, scan more, export again -- all in one afternoon."""
        with Sandbox() as box:
            first_path = box.csv(name="ManaBox_Collection.csv")
            first, _ = importer.stage_snapshot(first_path)

            rows = helpers.default_rows()
            rows.append(row("ToSort", "binder", SOL_B, "Sol Ring", "blc", "129", 4))
            second_path = box.csv(rows, name="ManaBox_Collection_v2.csv")
            os.utime(second_path, (0, os.path.getmtime(first_path)))
            second, _ = importer.stage_snapshot(second_path)

            self.assertNotEqual(first, second)
            self.assertEqual(len(os.listdir(paths.SNAPSHOTS)), 2)
            # The originals must both survive untouched.
            self.assertFalse(importer._same_bytes(first, second))

    def test_two_same_day_exports_produce_a_delta_in_the_right_order(self):
        with Sandbox() as box:
            conn = box.conn()
            first_path = box.csv(name="ManaBox_Collection.csv")
            importer.run_import(conn, first_path, offline=True, log=lambda *a: None)

            rows = helpers.default_rows()
            rows.append(row("ToSort", "binder", SOL_B, "Sol Ring", "blc", "129", 4))
            second_path = box.csv(rows, name="ManaBox_Collection_v2.csv")
            os.utime(second_path, (0, os.path.getmtime(first_path)))
            result = importer.run_import(conn, second_path, offline=True,
                                         log=lambda *a: None)

            self.assertEqual(importer.summarise(result["changes"]), {"added": 4})
