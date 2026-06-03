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
const traceList = document.querySelector("#trace-list");
const criteriaValues = Array.from(document.querySelectorAll(".criteria-grid dd"));
const resultCount = document.querySelector(".result-count");
const confidenceBadge = document.querySelector(".confidence-badge");
const heroFitScore = document.querySelector("#hero-fit-score");
const heroSkillRow = document.querySelector("#hero-skill-row");
const heroRole = document.querySelector("#hero-role");
const heroFormat = document.querySelector("#hero-format");
const topSlider = document.querySelector("#top-slider");
const topValue = document.querySelector("#top-value");
const signalBars = Array.from(document.querySelectorAll(".signal-grid span"));
const sourceInputs = Array.from(document.querySelectorAll('input[name="source"]'));

function buildLoadingTrace(sourceLabel) {
  return [
    {
      title: "Запрос принят",
      text: `Текст пользователя отправлен backend API. Источник: ${sourceLabel}.`,
    },
    {
      title: "Критерии извлекаются",
      text: "LLM и fallback-эвристики выделяют роль, уровень, город и навыки.",
    },
    {
      title: "Вакансии ищутся",
      text: "Backend обращается к выбранному источнику и получает карточки вакансий.",
    },
    {
      title: "Топ формируется",
      text: "Кандидаты валидируются, дедуплицируются и ранжируются.",
    },
  ];
}

const emptyTrace = [
  {
    title: "Запрос ожидается",
    text: "Агент готов принять описание кандидата.",
  },
  {
    title: "Критерии будут извлечены",
    text: "Роль, уровень, навыки, город, формат и зарплата.",
  },
  {
    title: "Вакансии будут найдены",
    text: "Выдача появится после запуска поиска.",
  },
  {
    title: "Топ будет сформирован",
    text: "Карточки отсортируются по score.",
  },
];

function renderTrace(items, activeIndex = null) {
  traceList.innerHTML = "";

  items.forEach((item, index) => {
    const li = document.createElement("li");
    const marker = document.createElement("span");
    const content = document.createElement("div");
    const title = document.createElement("strong");
    const text = document.createElement("p");

    marker.className = "trace-list__marker";
    title.textContent = item.title;
    text.textContent = item.text;
    content.append(title, text);
    li.append(marker, content);

    if (activeIndex !== null) {
      li.classList.toggle("is-complete", index < activeIndex);
      li.classList.toggle("is-active", index === activeIndex);
    }

    traceList.append(li);
  });
}

function setTraceState(activeIndex) {
  const items = Array.from(traceList.querySelectorAll("li"));
  items.forEach((item, index) => {
    item.classList.toggle("is-complete", index < activeIndex);
    item.classList.toggle("is-active", index === activeIndex);
  });
}

function resetTrace() {
  renderTrace(emptyTrace);
}

function hideResults() {
  criteriaPanel.hidden = true;
  vacanciesPanel.hidden = true;
  errorPanel.hidden = true;
}

function setButtonLoading(isLoading) {
  searchButton.disabled = isLoading;
  searchButton.querySelector("span").textContent = isLoading ? "Идет поиск" : "Найти вакансии";
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

    if (Array.isArray(value)) {
      target.classList.add("tag-row");
      target.innerHTML = "";
      if (!value.length) {
        target.textContent = "не указано";
        return;
      }
      value.forEach((skill) => {
        const tag = document.createElement("span");
        tag.textContent = skill;
        target.append(tag);
      });
      return;
    }

    target.classList.remove("tag-row");
    target.textContent = value || "не указано";
  });

  if (confidenceBadge) {
    confidenceBadge.textContent = "данные backend";
  }
}

function renderHeroSummary(criteria = {}, vacancies = []) {
  const skills = Array.isArray(criteria.skills) ? criteria.skills : [];
  const role = firstValue(criteria.role) || inferRole(textarea.value) || "Не выбрана";
  const format = formatSummary(criteria);
  const fitScore = bestScore(vacancies);

  if (heroRole) {
    heroRole.textContent = role;
  }
  if (heroFormat) {
    heroFormat.textContent = format;
  }
  if (heroFitScore) {
    heroFitScore.textContent = `${fitScore}%`;
  }
  if (heroSkillRow) {
    const labels = (skills.length ? skills : [role, "Навыки", format]).slice(0, 3);
    heroSkillRow.querySelectorAll("span").forEach((item, index) => {
      item.textContent = labels[index] || "—";
    });
  }

  updateSignalBars(vacancies);
}

function renderVacancies(vacancies, requestedTop, sourceLabel) {
  vacancyList.innerHTML = "";

  vacancies.forEach((vacancy) => {
    const card = cardTemplate.content.cloneNode(true);
    card.querySelector('[data-field="title"]').textContent = vacancy.title;
    card.querySelector('[data-field="company"]').textContent = vacancy.company;
    card.querySelector('[data-field="location"]').textContent = vacancy.location;
    card.querySelector('[data-field="salary"]').textContent = vacancy.salary;
    card.querySelector('[data-field="score"]').textContent = vacancy.score;
    card.querySelector('[data-field="source"]').textContent =
      vacancy.source_label || sourceLabel || getSelectedSourceLabel();
    card.querySelector('[data-field="why"]').textContent = vacancy.why;
    card.querySelector('[data-field="concern"]').textContent = vacancy.concern;
    card.querySelector('[data-field="next"]').textContent = vacancy.next;
    card.querySelector('[data-field="link"]').href = vacancy.link;
    vacancyList.append(card);
  });

  if (resultCount) {
    const label = sourceLabel || getSelectedSourceLabel();
    resultCount.textContent = `${vacancies.length} из ${requestedTop} показано · ${label}`;
  }
}

function renderBackendTrace(trace) {
  const items = normalizeTrace(trace);
  renderTrace(items, items.length);
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
  if (text.includes("Criteria")) return "Критерии извлечены";
  if (text.includes("Trudvsem request")) return "Запрос к Trudvsem";
  if (text.includes("SuperJob request")) return "Запрос к SuperJob";
  if (text.includes("Trudvsem returned")) return "Вакансии получены";
  if (text.includes("SuperJob returned")) return "Вакансии получены";
  if (text.includes("Validation")) return "Валидация завершена";
  if (text.includes("Scored")) return "Топ сформирован";
  if (text.includes("LLM")) return "LLM-шаг";
  if (text.includes("fallback")) return "Fallback";
  return index === 0 ? "Pipeline" : "Шаг агента";
}

async function runMockSearch(query) {
  const topN = getTopLimit();
  const source = getSelectedSource();
  const loadingTrace = buildLoadingTrace(getSelectedSourceLabel());
  hideResults();
  renderTrace(loadingTrace, 0);
  loadingPanel.hidden = false;
  setButtonLoading(true);
  renderHeroSummary({ role: inferRole(query), city: inferCity(query), remote: inferRemote(query) }, []);

  const progressTimer = window.setInterval(() => {
    const active = Array.from(traceList.querySelectorAll("li")).findIndex((item) =>
      item.classList.contains("is-active"),
    );
    const next = active < 0 ? 1 : Math.min(active + 1, loadingTrace.length - 1);
    setTraceState(next);
  }, 900);

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
    setTraceState(loadingTrace.length);
    loadingPanel.hidden = true;
    setButtonLoading(false);

    renderBackendTrace(payload.trace);
    renderCriteria(payload.criteria || {});
    renderVacancies(payload.vacancies || [], payload.top_n || topN, payload.source_label);
    renderHeroSummary(payload.criteria || {}, payload.vacancies || []);

    criteriaPanel.hidden = false;
    vacanciesPanel.hidden = false;
    vacanciesPanel.scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (error) {
    window.clearInterval(progressTimer);
    setButtonLoading(false);
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
    resetTrace();
    showError("Запрос пуст", "Добавьте роль, навыки и желаемый формат работы, чтобы агент начал поиск.");
    textarea.focus();
    return;
  }

  runMockSearch(query);
});

topSlider?.addEventListener("input", () => {
  if (topValue) {
    topValue.textContent = getTopLimit();
  }
});

function getTopLimit() {
  const value = Number.parseInt(topSlider?.value || "5", 10);
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

function updateSignalBars(vacancies) {
  const scores = vacancies.map((vacancy) => Number(vacancy.score)).filter(Number.isFinite);
  signalBars.forEach((bar, index) => {
    const score = scores[index % Math.max(scores.length, 1)] || 18 + index * 8;
    const height = Math.max(18, Math.min(96, Math.round(score)));
    bar.style.setProperty("--height", `${height}%`);
  });
}

function formatSummary(criteria) {
  const city = firstValue(criteria.city);
  const remote = String(criteria.remote || "").toLowerCase();
  if (city && remote.includes("да")) return `${city} / удаленно`;
  if (city) return city;
  if (remote.includes("да")) return "Удаленно";
  return "Не указан";
}

function firstValue(value) {
  if (Array.isArray(value)) return value[0] || "";
  return String(value || "").split(",")[0].trim();
}

function inferRole(query) {
  const text = query.toLowerCase();
  if (text.includes("продукт") && text.includes("аналит")) return "Продуктовый аналитик";
  if (text.includes("data analyst") || text.includes("аналитик данных")) return "Аналитик данных";
  if (text.includes("backend")) return "Backend";
  if (text.includes("qa") || text.includes("тестиров")) return "QA";
  return "";
}

function inferCity(query) {
  const text = query.toLowerCase();
  if (text.includes("моск")) return "Москва";
  if (text.includes("спб") || text.includes("петербург")) return "Санкт-Петербург";
  return "";
}

function inferRemote(query) {
  const text = query.toLowerCase();
  if (text.includes("удален") || text.includes("удалён") || text.includes("remote")) return "да";
  return "";
}

if (topValue) {
  topValue.textContent = getTopLimit();
}
renderHeroSummary();
resetTrace();
