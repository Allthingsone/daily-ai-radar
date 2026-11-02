"""Shared schema and display labels for the current paper-screening policy."""

PAPER_FLAGS = (
    "is_vla",
    "is_world_action_model",
    "is_representation_research",
    "is_embedding_model",
    "is_model_release",
    "is_robot_manipulation",
    "is_benchmark",
    "is_significant_contribution",
    "is_substantive_contribution",
)

PAPER_DIMENSION_LABELS = {
    "vla_wam_relevance": "VLA/WAM 相关性",
    "representation_relevance": "表征研究相关性",
    "embedding_relevance": "Embedding 模型相关性",
    "manipulation_benchmark_relevance": "机械臂基准相关性",
    "method_novelty": "方法新颖性",
    "evidence_quality": "证据质量",
    "reproducibility": "可复现性",
}

PAPER_CATEGORY_LABELS = {
    "vla-representation": "VLA 表征研究",
    "wam-representation": "WAM 表征研究",
    "embedding-model": "重要 Embedding 模型发布",
    "manipulation-benchmark": "重要机械臂 Benchmark",
    "other": "其他论文（不入选）",
}

PAPER_HEADING = "VLA/WAM 表征、重要 Embedding 模型与机械臂 Benchmark 论文"
