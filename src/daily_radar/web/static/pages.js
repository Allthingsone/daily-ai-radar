(() => {
  "use strict";

  const state = {
    kind: window.location.hash === "#papers" ? "paper" : "news",
    view: "important",
    paperPeriod: "today",
    query: "",
  };

  const kindButtons = [...document.querySelectorAll("[data-kind-button]")];
  const viewButtons = [...document.querySelectorAll("[data-view-button]")];
  const periodButtons = [...document.querySelectorAll("[data-paper-period]")];
  const panels = [...document.querySelectorAll("[data-kind-panel]")];
  const paperControls = document.querySelector("[data-paper-controls]");
  const search = document.querySelector("[data-radar-search]");

  const normalized = (value) =>
    value.normalize("NFKC").toLocaleLowerCase("zh-CN").trim();
  const recentPeriodLabel =
    periodButtons.find((button) => button.dataset.paperPeriod === "recent")
      ?.dataset.periodCopy || "近期";

  function emptyCopy(panel, visibleCount) {
    const empty = panel.querySelector("[data-filter-empty]");
    if (!empty) return;
    empty.hidden = visibleCount !== 0;
    const title = empty.querySelector("h3");
    const copy = empty.querySelector("p");

    if (state.query) {
      title.textContent = `没有匹配“${state.query}”的结果`;
      copy.textContent = "尝试更短的关键词，或清除搜索条件。";
    } else if (state.kind === "paper" && state.paperPeriod === "today") {
      title.textContent = `${panel.dataset.localDate || "今天"} 暂无符合双轴门槛的新论文`;
      copy.textContent = `系统不会用旧论文填充今日结果；可切换到${recentPeriodLabel}查看回补候选。`;
    } else if (state.view === "important") {
      title.textContent = "当前范围暂无精选结果";
      copy.textContent = "可切换到“全部”；系统不会用低分或未验证条目补足数量。";
    } else {
      title.textContent = "当前时间范围没有结果";
      copy.textContent = "下一次定时采集成功后页面会自动刷新。";
    }
  }

  function render() {
    kindButtons.forEach((button) => {
      const active = button.dataset.kindButton === state.kind;
      button.classList.toggle("active", active);
      button.setAttribute("aria-selected", String(active));
    });
    viewButtons.forEach((button) =>
      button.classList.toggle("active", button.dataset.viewButton === state.view),
    );
    periodButtons.forEach((button) =>
      button.classList.toggle(
        "active",
        button.dataset.paperPeriod === state.paperPeriod,
      ),
    );

    if (paperControls) paperControls.hidden = state.kind !== "paper";

    panels.forEach((panel) => {
      const active = panel.dataset.kindPanel === state.kind;
      panel.hidden = !active;
      if (!active) return;

      let visibleCount = 0;
      panel.querySelectorAll("[data-radar-card]").forEach((card) => {
        const matchesView =
          state.view === "all" || card.dataset.important === "true";
        const matchesPeriod =
          state.kind !== "paper" ||
          state.paperPeriod === "recent" ||
          card.dataset.today === "true";
        const matchesSearch =
          !state.query ||
          normalized(card.dataset.search || "").includes(normalized(state.query));
        const visible = matchesView && matchesPeriod && matchesSearch;
        card.hidden = !visible;
        if (visible) visibleCount += 1;
      });

      const count = panel.querySelector("[data-result-count]");
      if (count) count.textContent = String(visibleCount);
      const periodLabel = panel.querySelector("[data-period-label]");
      if (periodLabel) {
        periodLabel.textContent =
          state.paperPeriod === "today" ? "今日" : recentPeriodLabel;
      }
      emptyCopy(panel, visibleCount);
    });

    const hash = state.kind === "paper" ? "#papers" : "#news";
    if (window.location.hash !== hash) history.replaceState(null, "", hash);
  }

  kindButtons.forEach((button) => {
    button.addEventListener("click", () => {
      state.kind = button.dataset.kindButton;
      state.query = "";
      if (search) search.value = "";
      render();
    });
  });

  viewButtons.forEach((button) => {
    button.addEventListener("click", () => {
      state.view = button.dataset.viewButton;
      render();
    });
  });

  periodButtons.forEach((button) => {
    button.addEventListener("click", () => {
      state.paperPeriod = button.dataset.paperPeriod;
      render();
    });
  });

  if (search) {
    search.addEventListener("input", () => {
      state.query = search.value.trim();
      render();
    });
  }

  document.addEventListener("keydown", (event) => {
    if (event.key === "/" && document.activeElement !== search) {
      event.preventDefault();
      if (search) search.focus();
    }
    if (event.key === "Escape" && document.activeElement === search) {
      search.value = "";
      state.query = "";
      search.blur();
      render();
    }
  });

  window.addEventListener("hashchange", () => {
    state.kind = window.location.hash === "#papers" ? "paper" : "news";
    render();
  });

  render();
})();
