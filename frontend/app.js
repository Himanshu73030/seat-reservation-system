const POLL_INTERVAL_MS = 3000;
const PENDING_PREFIX = "seat-map.pending.";
const RESERVATIONS_KEY = "seat-map.reservations";

const elements = {
  showForm: document.querySelector("#show-form"),
  createForm: document.querySelector("#create-show-form"),
  createName: document.querySelector("#create-name"),
  createPrice: document.querySelector("#create-price"),
  createSeats: document.querySelector("#create-seats"),
  adminToken: document.querySelector("#admin-token"),
  createSubmit: document.querySelector("#create-submit"),
  createMessage: document.querySelector("#create-message"),
  showId: document.querySelector("#show-id"),
  showName: document.querySelector("#show-name"),
  showMeta: document.querySelector("#show-meta"),
  connection: document.querySelector("#connection-state"),
  connectionLabel: document.querySelector("#connection-label"),
  map: document.querySelector("#seat-map"),
  empty: document.querySelector("#empty-state"),
  updated: document.querySelector("#updated-at"),
  countConfirmed: document.querySelector("#count-confirmed"),
  countHeld: document.querySelector("#count-held"),
  countAvailable: document.querySelector("#count-available"),
  countTotal: document.querySelector("#count-total"),
  occupancy: document.querySelector("#occupancy-value"),
  occupancyTrack: document.querySelector("#occupancy-track"),
  occupancyBar: document.querySelector("#occupancy-bar"),
  selectionCount: document.querySelector("#selection-count"),
  selectionLabel: document.querySelector("#selection-label"),
  ticketPrice: document.querySelector("#ticket-price"),
  selectionTotal: document.querySelector("#selection-total"),
  selectedList: document.querySelector("#selected-list"),
  userToken: document.querySelector("#user-token"),
  reserve: document.querySelector("#reserve-button"),
  actionMessage: document.querySelector("#action-message"),
  myReservations: document.querySelector("#my-reservations"),
  reservationList: document.querySelector("#reservation-list"),
  toast: document.querySelector("#toast"),
};

let currentShow = null;
let selectedSeats = new Set();
let refreshInProgress = false;
let requestInProgress = false;
let toastTimer = null;

function pendingKey(showId) {
  return `${PENDING_PREFIX}${showId}`;
}

function readPending(showId) {
  try {
    const pending = JSON.parse(sessionStorage.getItem(pendingKey(showId)) || "null");
    return pending && pending.showId === showId ? pending : null;
  } catch {
    return null;
  }
}

function setMessage(message, kind = "") {
  elements.actionMessage.textContent = message;
  elements.actionMessage.dataset.kind = kind;
}

function setCreateMessage(message, kind = "") {
  elements.createMessage.textContent = message;
  elements.createMessage.dataset.kind = kind;
}

function notify(message) {
  window.clearTimeout(toastTimer);
  elements.toast.textContent = message;
  elements.toast.classList.add("visible");
  toastTimer = window.setTimeout(() => elements.toast.classList.remove("visible"), 2600);
}

function setConnection(state, label) {
  elements.connection.dataset.state = state;
  elements.connectionLabel.textContent = label;
}

function compareSeatIds(left, right) {
  return left.localeCompare(right, undefined, { numeric: true, sensitivity: "base" });
}

function formatPaise(amount) {
  const rupees = amount / 100n;
  const paise = (amount % 100n).toString().padStart(2, "0");
  return `₹${new Intl.NumberFormat("en-IN").format(rupees)}.${paise}`;
}

function updateSelection() {
  const selected = [...selectedSeats].sort(compareSeatIds);
  elements.selectionCount.textContent = String(selected.length);
  elements.selectionLabel.textContent = selected.length === 1 ? "seat selected" : "seats selected";
  const pricePaise = currentShow ? BigInt(currentShow.price_paise) : 0n;
  elements.selectionTotal.textContent = formatPaise(pricePaise * BigInt(selected.length));
  elements.selectedList.replaceChildren();
  if (selected.length === 0) {
    const hint = document.createElement("span");
    hint.className = "muted-copy";
    hint.textContent = "Select available seats on the map.";
    elements.selectedList.append(hint);
  } else {
    for (const seatId of selected) {
      const chip = document.createElement("span");
      chip.className = "selected-chip";
      chip.textContent = seatId;
      elements.selectedList.append(chip);
    }
  }
  renderSeats();
}

function updateCounts(show) {
  const counts = show.counts;
  elements.ticketPrice.textContent = formatPaise(BigInt(show.price_paise));
  elements.countConfirmed.textContent = String(counts.confirmed);
  elements.countHeld.textContent = String(counts.held);
  elements.countAvailable.textContent = String(counts.available);
  elements.countTotal.textContent = String(counts.total);
  const occupied = counts.confirmed + counts.held;
  const percentage = counts.total === 0 ? 0 : Math.round((occupied / counts.total) * 100);
  elements.occupancy.textContent = `${percentage}%`;
  elements.occupancyTrack.setAttribute("aria-valuenow", String(percentage));
  elements.occupancyBar.style.width = `${percentage}%`;
}

function renderSeats() {
  if (!currentShow) return;
  const fragment = document.createDocumentFragment();
  const orderedSeats = [...currentShow.seats].sort((left, right) => compareSeatIds(left.seat_id, right.seat_id));
  for (const seat of orderedSeats) {
    const button = document.createElement("button");
    const isSelected = selectedSeats.has(seat.seat_id) && seat.status === "available";
    button.type = "button";
    button.className = `seat seat-${seat.status}${isSelected ? " seat-selected" : ""}`;
    button.dataset.seatId = seat.seat_id;
    button.setAttribute("aria-label", `${seat.seat_id}, ${isSelected ? "selected" : seat.status}`);
    button.setAttribute("aria-pressed", String(isSelected));
    button.disabled = seat.status !== "available" || Boolean(readPending(currentShow.id));

    const label = document.createElement("span");
    label.className = "seat-label";
    label.textContent = seat.seat_id;
    button.append(label);
    button.addEventListener("click", () => {
      if (selectedSeats.has(seat.seat_id)) selectedSeats.delete(seat.seat_id);
      else selectedSeats.add(seat.seat_id);
      updateSelection();
    });
    fragment.append(button);
  }
  elements.map.replaceChildren(fragment);
}

function storedReservations() {
  try {
    const value = JSON.parse(sessionStorage.getItem(RESERVATIONS_KEY) || "[]");
    return Array.isArray(value) ? value : [];
  } catch {
    return [];
  }
}

function saveReservations(reservations) {
  sessionStorage.setItem(RESERVATIONS_KEY, JSON.stringify(reservations));
}

function renderReservations() {
  const reservations = storedReservations().filter((item) => item.showId === currentShow?.id);
  elements.reservationList.replaceChildren();
  elements.myReservations.hidden = reservations.length === 0;
  for (const reservation of reservations) {
    const row = document.createElement("div");
    row.className = "reservation-row";
    const seats = document.createElement("span");
    seats.className = "reservation-seats";
    seats.textContent = reservation.seats.join(", ");
    const cancel = document.createElement("button");
    cancel.type = "button";
    cancel.className = "cancel-button";
    cancel.textContent = "Cancel";
    cancel.disabled = requestInProgress;
    cancel.addEventListener("click", () => cancelReservation(reservation));
    row.append(seats, cancel);
    elements.reservationList.append(row);
  }
}

function renderShow(show) {
  currentShow = show;
  const validSeats = new Set(show.seats.filter((seat) => seat.status === "available").map((seat) => seat.seat_id));
  selectedSeats = new Set([...selectedSeats].filter((seatId) => validSeats.has(seatId)));
  elements.showId.value = show.id;
  elements.showName.textContent = show.name;
  elements.showMeta.textContent = `${show.id} · ${show.invariant_holds ? "Counts reconcile" : "Count mismatch"}`;
  elements.showMeta.dataset.kind = show.invariant_holds ? "ok" : "error";
  updateCounts(show);
  updateSelection();
  renderReservations();
  elements.updated.textContent = `Updated ${new Date().toLocaleTimeString()}`;
  elements.reserve.textContent = readPending(show.id) ? "Retry same request" : "Reserve selected";
  const pending = readPending(show.id);
  const tokenMatches = !pending || elements.userToken.value.trim() === pending.userId;
  elements.reserve.disabled = requestInProgress || (!pending && selectedSeats.size === 0) || !elements.userToken.value.trim() || !tokenMatches;
}

async function apiRequest(path, options = {}) {
  const response = await fetch(`/api${path}`, options);
  const text = await response.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = { detail: text || "Unexpected response" }; }
  return { response, data };
}

function parseSeatList(value) {
  const seats = value.split(/[\s,]+/).filter(Boolean);
  if (seats.length === 0) throw new Error("Enter at least one seat ID.");
  if (seats.length > 100000) throw new Error("A show can have at most 100,000 seats.");
  if (seats.some((seatId) => seatId.length > 32)) throw new Error("Seat IDs must be 32 characters or fewer.");
  if (new Set(seats).size !== seats.length) throw new Error("Seat IDs must be unique.");
  return seats;
}

async function createShow(event) {
  event.preventDefault();
  const name = elements.createName.value.trim();
  const pricePaiseText = elements.createPrice.value;
  const adminToken = elements.adminToken.value.trim();
  if (!name || name.length > 200) {
    setCreateMessage("Show name must contain 1 to 200 characters.", "error");
    return;
  }
  if (!/^(0|[1-9]\d*)$/.test(pricePaiseText) || Number(pricePaiseText) > 9000000000000000) {
    setCreateMessage("Enter a whole-number price in paise.", "error");
    return;
  }
  let seats;
  try {
    seats = parseSeatList(elements.createSeats.value);
  } catch (error) {
    setCreateMessage(error.message, "error");
    return;
  }
  if (!adminToken) {
    setCreateMessage("Enter the configured admin token.", "error");
    return;
  }

  elements.createSubmit.disabled = true;
  setCreateMessage("Creating show…", "pending");
  try {
    const { response, data } = await apiRequest("/shows", {
      method: "POST",
      headers: { Authorization: `Bearer ${adminToken}`, "Content-Type": "application/json" },
      body: JSON.stringify({ name, seats, price_paise: Number(pricePaiseText) }),
    });
    if (!response.ok) {
      const detail = Array.isArray(data?.detail)
        ? data.detail.map((issue) => issue.msg).join("; ")
        : data?.detail || `Request failed (${response.status})`;
      throw new Error(detail);
    }
    elements.showId.value = data.id;
    selectedSeats.clear();
    currentShow = null;
    elements.createForm.reset();
    elements.createForm.closest("details").open = false;
    setCreateMessage(`Created ${data.name} · ${data.id}`, "success");
    notify("Show created");
    await refreshShow();
  } catch (error) {
    setCreateMessage(error.message, "error");
  } finally {
    elements.createSubmit.disabled = false;
  }
}

async function refreshShow() {
  const showId = elements.showId.value.trim();
  if (!showId || refreshInProgress) return;
  refreshInProgress = true;
  elements.map.setAttribute("aria-busy", "true");
  try {
    const { response, data } = await apiRequest(`/shows/${encodeURIComponent(showId)}`);
    if (!response.ok) throw new Error(data?.detail || `Request failed (${response.status})`);
    if (!data || !Array.isArray(data.seats) || !data.counts) throw new Error("Show response is missing seat data");
    renderShow(data);
    setConnection("live", "Live · 3 sec refresh");
  } catch (error) {
    setConnection("error", "Connection issue");
    elements.updated.textContent = error.message;
    if (!currentShow || currentShow.id !== showId) {
      elements.showName.textContent = "Show unavailable";
      elements.showMeta.textContent = error.message;
      elements.map.replaceChildren(elements.empty);
      elements.empty.hidden = false;
    }
  } finally {
    refreshInProgress = false;
    elements.map.setAttribute("aria-busy", "false");
  }
}

function userHeaders(userId) {
  return { Authorization: `Bearer ${userId}`, "Content-Type": "application/json" };
}

async function reserveSelected() {
  if (!currentShow || requestInProgress) return;
  const userId = elements.userToken.value.trim();
  if (!userId) {
    setMessage("Enter your mock user ID first.", "error");
    elements.userToken.focus();
    return;
  }
  let pending = readPending(currentShow.id);
  if (pending && pending.userId !== userId) {
    setMessage("Enter the same user ID to retry this request.", "pending");
    return;
  }
  if (!pending) {
    const seats = [...selectedSeats].sort(compareSeatIds);
    if (seats.length === 0) return;
    pending = {
      showId: currentShow.id,
      userId,
      seats,
      idempotencyKey: crypto.randomUUID(),
    };
    sessionStorage.setItem(pendingKey(currentShow.id), JSON.stringify(pending));
  }

  requestInProgress = true;
  setMessage("Submitting reservation…", "pending");
  renderShow(currentShow);
  try {
    const { response, data } = await apiRequest(`/shows/${pending.showId}/reserve`, {
      method: "POST",
      headers: { ...userHeaders(pending.userId), "Idempotency-Key": pending.idempotencyKey },
      body: JSON.stringify({ seats: pending.seats }),
    });
    if (response.status >= 500) throw new Error(data?.detail || "Server error; retry using the same key.");
    sessionStorage.removeItem(pendingKey(pending.showId));
    if (response.status === 201) {
      const reservations = storedReservations();
      reservations.push({
        reservationId: data.reservation_id,
        showId: data.show_id,
        userId: data.user_id,
        seats: data.seats,
      });
      saveReservations(reservations);
      selectedSeats.clear();
      setMessage(`Reserved ${data.seats.join(", ")}.`, "success");
      notify("Reservation confirmed");
    } else {
      setMessage(data?.detail || "Reservation declined.", "error");
      notify("Reservation declined");
    }
  } catch (error) {
    setMessage(`${error.message} Your request key is saved for a safe retry.`, "pending");
  } finally {
    requestInProgress = false;
    renderReservations();
    await refreshShow();
  }
}

async function cancelReservation(reservation) {
  if (requestInProgress) return;
  if (elements.userToken.value.trim() !== reservation.userId) {
    setMessage("Enter the same user ID that made this reservation to cancel it.", "error");
    return;
  }
  requestInProgress = true;
  renderReservations();
  try {
    const { response, data } = await apiRequest(`/reservations/${reservation.reservationId}/cancel`, {
      method: "POST",
      headers: userHeaders(reservation.userId),
    });
    if (!response.ok) throw new Error(data?.detail || `Cancellation failed (${response.status})`);
    saveReservations(storedReservations().filter((item) => item.reservationId !== reservation.reservationId));
    setMessage(`Released ${reservation.seats.join(", ")}.`, "success");
    notify("Reservation cancelled");
  } catch (error) {
    setMessage(error.message, "error");
  } finally {
    requestInProgress = false;
    renderReservations();
    await refreshShow();
  }
}

elements.showForm.addEventListener("submit", (event) => {
  event.preventDefault();
  selectedSeats.clear();
  currentShow = null;
  setMessage("");
  refreshShow();
});
elements.createForm.addEventListener("submit", createShow);
elements.reserve.addEventListener("click", reserveSelected);
elements.userToken.addEventListener("input", () => {
  if (currentShow) renderShow(currentShow);
});

window.setInterval(refreshShow, POLL_INTERVAL_MS);
const initialShow = new URLSearchParams(window.location.search).get("show");
if (initialShow) {
  elements.showId.value = initialShow;
  refreshShow();
}