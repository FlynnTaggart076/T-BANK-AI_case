const API_ENDPOINT = "/api/search";

const form = document.querySelector("#search-form");
const textarea = document.querySelector("#query");
const searchButton = document.querySelector("#search-button");
const loadingPanel = document.querySelector("#loading-panel");
const errorPanel = document.querySelector("#error-panel");
const errorTitle = document.querySelector("#error-title");
const errorMessage = document.querySelector("#error-message");
const criteriaPanel = document.querySelector("#criteria-panel");
const vacanciesPanel = document.querySelector("#vacancies-panel");
const vacancyList = document.querySelector("#vacancy-list");
const cardTemplate = document.querySelector("#vacancy-card-template");
const tracePanel = document.querySelector("#trace-panel");
const traceList = document.querySelector("#trace-list");
const traceSummaryText = document.querySelector("#trace-summary-text");
const criteriaValues = Array.from(document.querySelectorAll(".criteria-grid dd"));
const resultCount = document.querySelector(".result-count");
const confidenceBadge = document.querySelector(".confidence-badge");
const topSlider = document.querySelector("#top-slider");
const topValue = document.querySelector("#top-value");
const sourceInputs = Array.from(document.querySelectorAll('input[name="source"]'));
const exampleButtons = Array.from(document.querySelectorAll("[data-query]"));

const summaryStatus = document.querySelector("#summary-status");
const summaryScore = document.querySelector("#summary-score");
const scoreRing = document.querySelector("#score-ring");
const summaryMessage = document.querySelector("#summary-message");
const summaryDetail = document.querySelector("#summary-detail");
const summarySource = document.querySelector("#summary-source");
const summaryRole = document.querySelector("#summary-role");
const summaryCity = document.querySelector("#summary-city");
const summarySalary = document.querySelector("#summary-salary");
const summaryCount = document.querySelector("#summary-count");
const pipelineLabel = document.querySelector("#pipeline-label");
const pipelineBarFill = document.querySelector("#pipeline-bar-fill");
const pipelineSteps = Array.from(document.querySelectorAll("#pipeline-steps li"));

const emptyTrace = [
  {
    title: "Запрос ожидается",
    text: "Опишите желаемую работу и запустите поиск.",
  },
  {
    title: "Критерии",
    text: "Агент выделит роль, уровень, навыки, город, формат и зарплату.",
  },
  {
    title: "Источники",
    text: "Вакансии будут получены из выбранных источников.",
  },
  {
    title: "Ранжирование",
    text: "Подходящие карточки будут отсортированы по score.",
  },
];

function buildLoadingTrace(sourceLabel) {
  return [
    {
      title: "Запрос принят",
      text: `Описание отправлено агенту. Источник: ${sourceLabel}.`,
    },
    {
      title: "Критерии извлекаются",
      text: "Модель определяет роль, город, опыт, навыки и ограничения.",
    },
    {
      title: "Вакансии ищутся",
      text: "Backend обращается к источникам и собирает кандидатов.",
    },
    {
      title: "Топ формируется",
      text: "Вакансии проверяются, очищаются от дублей и ранжируются.",
    },
  ];
}

function renderTrace(items, activeIndex = null) {
  traceList.innerHTML = "";

  items.forEach((item, index) => {
    const row = document.createElement("li");
    const marker = document.createElement("span");
    const content = document.createElement("div");
    const title = document.createElement("strong");
    const text = document.createElement("p");

    marker.className = "trace-list__marker";
    title.textContent = item.title;
    text.textContent = item.text;
    content.append(title, text);
    row.append(marker, content);

    if (activeIndex !== null) {
      row.classList.toggle("is-complete", index < activeIndex);
      row.classList.toggle("is-active", index === activeIndex);
    }

    traceList.append(row);
  });
}

function setTraceState(activeIndex) {
  const items = Array.from(traceList.querySelectorAll("li"));
  items.forEach((item, index) => {
    item.classList.toggle("is-complete", index < activeIndex);
    item.classList.toggle("is-active", index === activeIndex);
  });
}

function renderBackendTrace(trace) {
  const items = normalizeTrace(trace);
  renderTrace(items, items.length);
  traceSummaryText.textContent = `${items.length} этапов выполнено`;
}

function normalizeTrace(trace) {
  if (!Array.isArray(trace) || !trace.length) {
    return buildLoadingTrace(getSelectedSourceLabel());
  }

  return trace.slice(0, 12).map((line, index) => {
    const text = String(line);
    return {
      title: traceTitle(text, index),
      text,
    };
  });
}

function traceTitle(text, index) {
  const normalized = text.toLowerCase();
  if (normalized.includes("запрос принят")) return "Запрос принят";
  if (normalized.includes("критери")) return "Критерии извлечены";
  if (normalized.includes("источник")) return "Источники выбраны";
  if (normalized.includes("получено вакансий")) return "Вакансии получены";
  if (normalized.includes("дубл")) return "Дубли удалены";
  if (normalized.includes("проверен")) return "Кандидаты проверены";
  if (normalized.includes("топ")) return "Топ сформирован";
  if (normalized.includes("superjob")) return "Шаг SuperJob";
  if (normalized.includes("работа россии") || normalized.includes("trudvsem")) {
    return "Шаг Работа России";
  }
  return index === 0 ? "Pipeline" : `Шаг ${index + 1}`;
}

function hideResultPanels() {
  criteriaPanel.hidden = true;
  vacanciesPanel.hidden = true;
  errorPanel.hidden = true;
}

function setButtonLoading(isLoading) {
  searchButton.disabled = isLoading;
  searchButton.querySelector("span").textContent = isLoading
    ? "Агент ищет..."
    : "Найти вакансии";
}

function showError(title, message, trace = []) {
  loadingPanel.hidden = true;
  criteriaPanel.hidden = true;
  vacanciesPanel.hidden = true;
  errorTitle.textContent = title;
  errorMessage.textContent = message;
  errorPanel.hidden = false;

  if (trace.length) {
    renderBackendTrace(trace);
  }
}

function renderCriteria(criteria) {
  const values = [
    criteria.role,
    criteria.level,
    criteria.skills,
    criteria.city,
    criteria.remote,
    criteria.salary,
  ];

  values.forEach((value, index) => {
    const target = criteriaValues[index];
    if (!target) return;

    target.classList.remove("tag-row");
    target.innerHTML = "";

    if (Array.isArray(value)) {
      if (!value.length) {
        target.textContent = "не указано";
        return;
      }

      target.classList.add("tag-row");
      value.forEach((skill) => {
        const tag = document.createElement("span");
        tag.textContent = skill;
        target.append(tag);
      });
      return;
    }

    target.textContent = cleanDisplayValue(value, "не указано");
  });

  if (confidenceBadge) {
    confidenceBadge.textContent = "Данные backend";
  }
}

function renderVacancies(vacancies, requestedTop, sourceLabel) {
  vacancyList.innerHTML = "";

  vacancies.forEach((vacancy) => {
    const card = cardTemplate.content.cloneNode(true);
    const score = Number(vacancy.score) || 0;
    const sourceBadge = card.querySelector('[data-field="source"]');
    const scoreBadge = card.querySelector(".score-badge");

    card.querySelector('[data-field="title"]').textContent = vacancy.title;
    card.querySelector('[data-field="company"]').textContent = vacancy.company;
    card.querySelector('[data-field="location"]').textContent = vacancy.location;
    card.querySelector('[data-field="salary"]').textContent = vacancy.salary;
    card.querySelector('[data-field="score"]').textContent = Math.round(score);
    sourceBadge.textContent = vacancy.source_label || sourceLabel || getSelectedSourceLabel();
    sourceBadge.dataset.source = vacancy.source || "";
    card.querySelector('[data-field="why"]').textContent = vacancy.why;
    card.querySelector('[data-field="concern"]').textContent = vacancy.concern;
    card.querySelector('[data-field="next"]').textContent = vacancy.next;
    card.querySelector('[data-field="link"]').href = vacancy.link;

    scoreBadge.classList.toggle("is-high", score >= 80);
    scoreBadge.classList.toggle("is-medium", score >= 60 && score < 80);
    scoreBadge.classList.toggle("is-low", score < 60);
    vacancyList.append(card);
  });

  if (resultCount) {
    const label = sourceLabel || getSelectedSourceLabel();
    resultCount.textContent = `${vacancies.length} из ${requestedTop} · ${label}`;
  }
}

function updateSummary({
  status = "Ожидание",
  statusClass = "",
  score = 0,
  message = "Запрос еще не выполнен",
  detail = "После поиска здесь появится краткая сводка результата.",
  source = getSelectedSourceLabel(),
  role = "Не определена",
  city = "Не указан",
  salary = "Не указана",
  count = 0,
  top = getTopLimit(),
} = {}) {
  const normalizedScore = Math.max(0, Math.min(100, Math.round(Number(score) || 0)));

  summaryStatus.textContent = status;
  summaryStatus.className = `summary-status${statusClass ? ` ${statusClass}` : ""}`;
  summaryScore.textContent = normalizedScore;
  scoreRing.style.setProperty("--score", normalizedScore);
  summaryMessage.textContent = message;
  summaryDetail.textContent = detail;
  summarySource.textContent = source;
  summaryRole.textContent = cleanDisplayValue(role, "Не определена");
  summaryCity.textContent = cleanDisplayValue(city, "Не указан");
  summarySalary.textContent = cleanDisplayValue(salary, "Не указана");
  summaryCount.textContent = `${count} из ${top}`;
}

function updateSummaryFromResult(criteria, vacancies, sourceLabel, requestedTop) {
  const score = bestScore(vacancies);
  updateSummary({
    status: vacancies.length ? "Готово" : "Нет результатов",
    statusClass: vacancies.length ? "is-complete" : "is-error",
    score,
    message: vacancies.length
      ? `Найдено ${vacancies.length} подходящих вакансий`
      : "Строгий фильтр не нашел совпадений",
    detail: vacancies.length
      ? `Лучший результат получил fit score ${score} из 100.`
      : "Измените формулировку, источник или ограничения поиска.",
    source: sourceLabel || getSelectedSourceLabel(),
    role: criteria.role,
    city: criteria.city,
    salary: criteria.salary,
    count: vacancies.length,
    top: requestedTop,
  });
}

function setPipelineState(stage, label) {
  const safeStage = Math.max(0, Math.min(4, stage));
  const progress = safeStage === 0 ? 0 : (safeStage / 4) * 100;

  pipelineBarFill.style.width = `${progress}%`;
  pipelineLabel.textContent = label;
  pipelineSteps.forEach((item, index) => {
    item.classList.toggle("is-complete", index < safeStage);
    item.classList.toggle("is-active", index === safeStage && safeStage < 4);
  });
}

async function runSearch(query) {
  const topN = getTopLimit();
  const source = getSelectedSource();
  const sourceLabel = getSelectedSourceLabel();
  const loadingTrace = buildLoadingTrace(sourceLabel);

  hideResultPanels();
  loadingPanel.hidden = false;
  setButtonLoading(true);
  renderTrace(loadingTrace, 0);
  traceSummaryText.textContent = "Агент выполняет поиск";
  updateSummary({
    status: "Анализ",
    statusClass: "is-loading",
    message: "Агент анализирует запрос",
    detail: "Обычно поиск занимает несколько секунд.",
    source: sourceLabel,
    count: 0,
    top: topN,
  });
  setPipelineState(1, "Запрос принят");

  let progressStage = 1;
  const progressLabels = ["Запрос принят", "Извлекаем критерии", "Ищем вакансии", "Формируем топ"];
  const progressTimer = window.setInterval(() => {
    progressStage = Math.min(progressStage + 1, 3);
    setTraceState(progressStage);
    setPipelineState(progressStage, progressLabels[progressStage - 1]);
  }, 950);

  try {
    const response = await fetch(API_ENDPOINT, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ query, top_n: topN, source }),
    });

    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(payload.message || "Backend API вернул ошибку.");
    }

    window.clearInterval(progressTimer);
    loadingPanel.hidden = true;
    setButtonLoading(false);
    setPipelineState(4, "Поиск завершен");
    renderBackendTrace(payload.trace);
    renderCriteria(payload.criteria || {});

    const vacancies = Array.isArray(payload.vacancies) ? payload.vacancies : [];
    updateSummaryFromResult(
      payload.criteria || {},
      vacancies,
      payload.source_label,
      payload.top_n || topN,
    );

    if (!vacancies.length) {
      showError(
        "Вакансии не найдены",
        "Строгий фильтр не нашел подходящих вакансий. Попробуйте другой источник, соседний город или более широкую формулировку роли.",
        payload.trace || [],
      );
      return;
    }

    renderVacancies(vacancies, payload.top_n || topN, payload.source_label);
    criteriaPanel.hidden = false;
    vacanciesPanel.hidden = false;
    criteriaPanel.scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (error) {
    window.clearInterval(progressTimer);
    setButtonLoading(false);
    setPipelineState(0, "Поиск остановлен");
    updateSummary({
      status: "Ошибка",
      statusClass: "is-error",
      message: "Не удалось выполнить поиск",
      detail: "Проверьте, запущен ли backend, и повторите запрос.",
      source: sourceLabel,
      count: 0,
      top: topN,
    });
    traceSummaryText.textContent = "Поиск завершился ошибкой";
    showError(
      "Backend недоступен",
      `${error.message} Запустите сервер командой: py server.py`,
    );
  }
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  const query = textarea.value.trim();

  if (!query) {
    renderTrace(emptyTrace);
    showError(
      "Запрос пуст",
      "Добавьте роль, навыки или желаемый формат работы, чтобы агент начал поиск.",
    );
    textarea.focus();
    return;
  }

  runSearch(query);
});

exampleButtons.forEach((button) => {
  button.addEventListener("click", () => {
    textarea.value = button.dataset.query || "";
    textarea.focus();
  });
});

topSlider.addEventListener("input", () => {
  const top = getTopLimit();
  topValue.textContent = top;
  summaryCount.textContent = `0 из ${top}`;
});

sourceInputs.forEach((input) => {
  input.addEventListener("change", () => {
    summarySource.textContent = getSelectedSourceLabel();
  });
});

function getTopLimit() {
  const value = Number.parseInt(topSlider.value || "5", 10);
  return Math.max(1, Math.min(25, Number.isNaN(value) ? 5 : value));
}

function getSelectedSource() {
  return sourceInputs.find((input) => input.checked)?.value || "trudvsem";
}

function getSelectedSourceLabel() {
  const labels = {
    trudvsem: "Работа России",
    superjob: "SuperJob",
    all: "Работа России + SuperJob",
  };
  return labels[getSelectedSource()] || labels.trudvsem;
}

function bestScore(vacancies) {
  const scores = vacancies.map((vacancy) => Number(vacancy.score)).filter(Number.isFinite);
  if (!scores.length) return 0;
  return Math.max(0, Math.min(100, Math.round(Math.max(...scores))));
}

function cleanDisplayValue(value, fallback) {
  if (Array.isArray(value)) {
    return value.filter(Boolean).join(", ") || fallback;
  }

  const text = String(value || "").trim();
  const normalized = text.toLowerCase();
  if (!text || normalized === "не указано" || normalized === "не указана") {
    return fallback;
  }
  return text;
}

topValue.textContent = getTopLimit();
renderTrace(emptyTrace);
updateSummary();
setPipelineState(0, "Готов к запуску");
