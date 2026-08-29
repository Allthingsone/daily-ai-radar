import unittest
from datetime import datetime, timedelta, timezone

from daily_radar.models import RadarItem
from daily_radar.processing.normalize import canonicalize_url, fingerprint_title
from daily_radar.processing.scoring import (
    classify_paper,
    news_event_gate,
    paper_gate,
    score_news,
    score_paper,
)


def make_item(kind, title, summary, source_focus=1.0):
    url = "https://example.com/" + fingerprint_title(title)
    return RadarItem(
        kind=kind,
        title=title,
        summary=summary,
        url=url,
        canonical_url=canonicalize_url(url),
        source_id="test",
        source_name="Test Primary Source",
        source_tier=1,
        source_type="official" if kind == "news" else "paper-api",
        source_focus=source_focus,
        published_at=datetime.now(timezone.utc) - timedelta(hours=2),
        fingerprint=fingerprint_title(title),
    )


class ScoringTests(unittest.TestCase):
    def test_high_impact_ai_release_scores_above_generic_story(self):
        release = make_item(
            "news",
            "New open-source multimodal reasoning model released",
            "The large language model establishes a new benchmark.",
        )
        generic = make_item(
            "news",
            "Company publishes its weekly office update",
            "A short update for employees.",
            source_focus=0.2,
        )
        score_news(release)
        score_news(generic)
        self.assertGreater(release.score, generic.score)
        self.assertGreaterEqual(release.component_scores["relevance"], 20)

    def test_news_gate_accepts_general_ai_model_release(self):
        release = make_item(
            "news",
            "OpenAI releases GPT-6 reasoning model",
            "The new model and weights are now available through an API.",
        )
        passed, evidence = news_event_gate(release)
        self.assertTrue(passed)
        self.assertEqual(evidence["event_type"], "model-release")

    def test_news_gate_accepts_ai_research_result_outside_driving(self):
        result = make_item(
            "news",
            "AI system discovers a new protein-folding mechanism",
            "Researchers demonstrate the result in a controlled study and publish experiments.",
        )
        passed, evidence = news_event_gate(result)
        self.assertTrue(passed)
        self.assertEqual(evidence["event_type"], "research-result")

    def test_news_gate_rejects_opinion_and_legal_stories(self):
        opinion = make_item(
            "news",
            "CEO joins debate about parenting with ChatGPT",
            "An interview discusses personal AI usage and opinions.",
        )
        legal = make_item(
            "news",
            "Judge announces decision in lawsuit over an AI model",
            "The court decision concerns regulation and policy.",
        )
        self.assertFalse(news_event_gate(opinion)[0])
        self.assertFalse(news_event_gate(legal)[0])

    def test_news_gate_rejects_non_ai_product_launch_from_focused_feed(self):
        product = make_item(
            "news",
            "Company launches a physical phone lock",
            "The new tool blocks distracting mobile applications.",
        )
        self.assertFalse(news_event_gate(product)[0])

    def test_news_gate_rejects_sponsored_enterprise_content(self):
        sponsored = make_item(
            "news",
            "How a vendor closes the last mile for AI agents",
            "Presented by Example Corp. Sponsored content discusses models and platforms.",
        )
        self.assertFalse(news_event_gate(sponsored)[0])

    def test_media_story_needs_event_signal_anchored_in_title(self):
        explainer = make_item(
            "news",
            "When graph retrieval actually beats vector retrieval",
            "This article reviews an older LLM paper that introduced a model and four benchmark studies.",
        )
        explainer.source_type = "media"
        self.assertFalse(news_event_gate(explainer)[0])

    def test_media_model_achievement_can_pass_without_driving_terms(self):
        achievement = make_item(
            "news",
            "MiniMax H3 is the first open model to top an AI video ranking",
            "MiniMax releases H3 video model weights for public use.",
        )
        achievement.source_type = "media"
        passed, evidence = news_event_gate(achievement)
        self.assertTrue(passed)
        self.assertEqual(evidence["event_type"], "model-release")

    def test_open_agent_framework_is_not_mislabeled_as_model_release(self):
        framework = make_item(
            "news",
            "Orchard: An open framework for scalable agentic AI",
            "Researchers release the framework for building and evaluating AI agents.",
        )
        passed, evidence = news_event_gate(framework)
        self.assertTrue(passed)
        self.assertEqual(evidence["event_type"], "open-source-tool")

    def test_scored_news_records_release_evidence(self):
        release = make_item(
            "news",
            "New open-source speech model released",
            "The AI model ships with weights and a public benchmark.",
        )
        score_news(release)
        self.assertTrue(release.metadata["news_gate"]["passed"])
        self.assertTrue(any("发布/成果门槛通过" in reason for reason in release.reasons))

    def test_paper_gate_requires_both_axes(self):
        robotics_only = make_item(
            "paper",
            "A Vision-Language-Action Model for Robot Manipulation",
            "We evaluate a VLA policy on tabletop grasping.",
        )
        driving_only = make_item(
            "paper",
            "Trajectory Planning for Autonomous Driving",
            "A classical optimizer plans safe driving trajectories.",
        )
        direct = make_item(
            "paper",
            "DriveVLA for End-to-End Autonomous Driving",
            "A vision-language-action driving agent is tested in closed-loop CARLA.",
        )
        self.assertFalse(paper_gate(robotics_only)[0])
        self.assertFalse(paper_gate(driving_only)[0])
        self.assertTrue(paper_gate(direct)[0])

    def test_scored_paper_retains_audit_reasons(self):
        paper = make_item(
            "paper",
            "MLLM-Drive: Multimodal Large Language Models for Autonomous Driving",
            "We propose a driving agent and run experiments on NAVSIM benchmark.",
        )
        passed, scored = score_paper(paper, ["driving agent"])
        self.assertTrue(passed)
        self.assertIn("domain_relevance", scored.component_scores)
        self.assertTrue(any("模型轴" in reason for reason in scored.reasons))

    def test_benchmark_mention_does_not_hide_reasoning_contribution(self):
        title = "Outcome-Guided Distillation for VLM Reasoning in Autonomous Driving"
        text = (
            "A multimodal reasoning model for autonomous driving. "
            "Experiments compare it on a public benchmark."
        )
        self.assertEqual(classify_paper(text, title), "reasoning-vla")


if __name__ == "__main__":
    unittest.main()
