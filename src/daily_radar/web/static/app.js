document.addEventListener("click", async (event) => {
  const button = event.target.closest("button[data-feedback]");
  if (!button) return;

  const card = button.closest("[data-item-id]");
  const itemId = card.dataset.itemId;
  const value = button.dataset.feedback;
  button.disabled = true;

  try {
    const response = await fetch(`/api/items/${itemId}/feedback`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ value }),
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    card.querySelectorAll("button[data-feedback]").forEach((node) => {
      node.classList.toggle("selected", node === button);
    });
  } catch (error) {
    window.alert(`反馈保存失败：${error.message}`);
  } finally {
    button.disabled = false;
  }
});

