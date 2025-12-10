'use strict';

const STORAGE_KEYS = {
  doctor: 'aims_doctor',
  patient: 'aims_patient',
  summary: 'aims_summary',
  symptoms: 'aims_symptoms',
  consultationId: 'aims_consultation_id'
};

let mediaRecorder;
let recordedChunks = [];
let existingPatientUhid = '';

const navSteps = [
  { id: 'doctor', label: 'Doctor Info', href: 'index.html' },
  { id: 'patient', label: 'Patient Registration', href: 'patient.html' },
  { id: 'consultation', label: 'Consultation', href: 'consultation.html' },
  { id: 'summary', label: 'Summary', href: 'summary.html' }
];

function setActiveNav(step) {
  document.querySelectorAll('.progress-link').forEach(link => {
    const isActive = link.dataset.step === step;
    link.classList.toggle('active', isActive);
  });
}

function updateHeaderClock() {
  const headerTimeElement = document.getElementById('headerTime');
  if (!headerTimeElement) return;
  const now = new Date();
  const timeStr = now.toLocaleTimeString('en-CA', {
    hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: true
  });
  const dateStr = now.toLocaleDateString('en-CA', {
    day: '2-digit', month: 'short', year: 'numeric'
  });
  headerTimeElement.textContent = `${dateStr} • ${timeStr}`;
}

function startHeaderClock() {
  updateHeaderClock();
  setInterval(updateHeaderClock, 1000);
}

function saveToStorage(key, value) {
  localStorage.setItem(key, JSON.stringify(value));
}

function readFromStorage(key) {
  const raw = localStorage.getItem(key);
  if (!raw) return null;
  try {
    return JSON.parse(raw);
  } catch (err) {
    console.warn('Failed to parse storage value', key, err);
    return null;
  }
}

function getDoctorInfo() {
  return readFromStorage(STORAGE_KEYS.doctor);
}

function setDoctorInfo(info) {
  saveToStorage(STORAGE_KEYS.doctor, info);
}

function getPatientInfo() {
  return readFromStorage(STORAGE_KEYS.patient);
}

function setPatientInfo(info) {
  saveToStorage(STORAGE_KEYS.patient, info);
}

function setSummaryData(summary) {
  saveToStorage(STORAGE_KEYS.summary, summary);
}

function getSummaryData() {
  return readFromStorage(STORAGE_KEYS.summary);
}

function setSymptomsData(data) {
  saveToStorage(STORAGE_KEYS.symptoms, data);
}

function getSymptomsData() {
  return readFromStorage(STORAGE_KEYS.symptoms);
}

function setConsultationId(id) {
  saveToStorage(STORAGE_KEYS.consultationId, id);
}

function getConsultationId() {
  return readFromStorage(STORAGE_KEYS.consultationId);
}

function showToast(message) {
  alert(message);
}

function composeSummaryWithContext(summaryText) {
  const doctor = getDoctorInfo() || {};
  const patient = getPatientInfo() || {};
  const lines = [];
  lines.push('============================================================================');
  lines.push('VISIT CONTEXT');
  lines.push('============================================================================');
  lines.push(`Doctor: ${doctor.name || 'N/A'}${doctor.department ? ` | Dept: ${doctor.department}` : ''}${doctor.designation ? ` | ${doctor.designation}` : ''}`);
  lines.push(`Patient: ${patient.name || 'N/A'} | UHID: ${patient.uhid || 'Pending'}`);
  const demoParts = [];
  if (patient.sex) demoParts.push(`Sex: ${patient.sex}`);
  if (patient.age) demoParts.push(`Age: ${patient.age}`);
  if (patient.dob) demoParts.push(`DOB: ${patient.dob}`);
  lines.push(demoParts.length ? demoParts.join(' | ') : '');
  if (patient.visitDateTime) {
    lines.push(`Visit Date/Time: ${patient.visitDateTime}`);
  }
  const addressParts = [patient.unitSuite || patient.unit_suite, patient.street, patient.city, patient.province, patient.postalCode || patient.postal_code].filter(Boolean);
  if (addressParts.length) {
    lines.push(`Address: ${addressParts.join(', ')}`);
  }
  lines.push('');
  const summarySection = summaryText || 'No summary generated.';
  return `${lines.filter(Boolean).join('\n')}${summarySection ? '\n\n' + summarySection : ''}`.trim();
}

async function persistDoctor(payload) {
  const form = new FormData();
  Object.entries(payload).forEach(([key, value]) => form.append(key, value ?? ''));
  const res = await fetch('/doctors', {
    method: 'POST',
    body: form,
  });
  const data = await res.json();
  if (data.error) {
    throw new Error(data.error);
  }
  return data.doctor;
}

async function persistPatient(payload, existingUhidValue) {
  const form = new FormData();
  Object.entries(payload).forEach(([key, value]) => form.append(key, value ?? ''));
  if (existingUhidValue) {
    form.append('existing_uhid', existingUhidValue);
  }
  const res = await fetch('/patients', { method: 'POST', body: form });
  const data = await res.json();
  if (data.error) {
    throw new Error(data.error);
  }
  return data.patient;
}

function updateAutoUhidDisplay(value) {
  const display = document.getElementById('autoUhidValue');
  if (display) {
    display.textContent = value || 'Will be assigned after saving';
  }
}

function populateProgressNav() {
  const navContainer = document.getElementById('progressNav');
  if (!navContainer) return;
  navContainer.innerHTML = navSteps.map(step => `
    <a class="progress-link" data-step="${step.id}" href="${step.href}">
      <span class="step-number">${navSteps.indexOf(step) + 1}</span>
      <span class="step-label">${step.label}</span>
    </a>
  `).join('');
}

// ---------------------- Doctor Page ----------------------
function setupDoctorPage() {
  const form = document.getElementById('doctorForm');
  if (!form) return;

  const stored = getDoctorInfo();
  if (stored) {
    form.doctorName.value = stored.name || '';
    form.department.value = stored.department || '';
    form.designation.value = stored.designation || '';
    form.patientType.value = stored.patient_type || stored.patientType || '';
  }

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const payload = {
      name: form.doctorName.value.trim(),
      department: form.department.value.trim(),
      designation: form.designation.value.trim(),
      patient_type: form.patientType.value.trim()
    };

    if (!payload.name || !payload.department || !payload.designation || !payload.patient_type) {
      return showToast('Please complete all doctor fields.');
    }

    try {
      const doctor = await persistDoctor(payload);
      setDoctorInfo(doctor);
      showToast('Doctor saved successfully.');
      window.location.href = 'patient.html';
    } catch (err) {
      showToast(`Doctor save failed: ${err.message}`);
    }
  });
}

// ---------------------- Patient Page ----------------------
function resetPatientFormFields(form) {
  form.reset();
  existingPatientUhid = '';
  updateAutoUhidDisplay('');
  if (form.visitDateTime) {
    form.visitDateTime.value = new Date().toLocaleString('en-CA');
  }
  document.getElementById('historyList')?.replaceChildren();
  const historySection = document.getElementById('consultationHistory');
  if (historySection) historySection.classList.add('hidden');
  const searchResult = document.getElementById('searchResult');
  if (searchResult) searchResult.innerHTML = '';
  setPatientInfo(null);
}

function populatePatientForm(form, patient) {
  form.patientName.value = patient.name || '';
  form.sex.value = patient.sex || '';
  form.age.value = patient.age || '';
  form.dob.value = patient.dob || '';
  form.phone.value = patient.phone || '';
  form.email.value = patient.email || '';
  form.unitSuite.value = patient.unit_suite || patient.unitSuite || '';
  form.street.value = patient.street || '';
  form.city.value = patient.city || '';
  form.province.value = patient.province || '';
  form.postalCode.value = patient.postal_code || patient.postalCode || '';
  if (patient.visitDateTime && form.visitDateTime) {
    form.visitDateTime.value = patient.visitDateTime;
  }
  updateAutoUhidDisplay(patient.uhid);
  existingPatientUhid = patient.uhid || '';
}

function displayConsultationHistory(consultations) {
  const historySection = document.getElementById('consultationHistory');
  const historyList = document.getElementById('historyList');
  if (!historySection || !historyList) return;
  historyList.innerHTML = '';

  if (!consultations || consultations.length === 0) {
    historySection.classList.add('hidden');
    return;
  }

  historySection.classList.remove('hidden');
  consultations.forEach(consult => {
    const card = document.createElement('div');
    card.className = 'history-card';
    const visitDate = new Date(consult.visit_datetime).toLocaleString('en-CA');
    card.innerHTML = `
      <h4>
        <span>📋 Visit: ${visitDate}</span>
        <span class="history-badge">${consult.symptom_count} Symptoms</span>
      </h4>
      <p><strong>Doctor:</strong> ${consult.doctor_name} (${consult.doctor_department})</p>
      <p><strong>Summary:</strong> ${consult.summary || 'No summary available'}</p>
    `;
    card.addEventListener('click', () => viewConsultationDetails(consult.id));
    historyList.appendChild(card);
  });
}

function viewConsultationDetails(consultationId) {
  if (!consultationId) return;
  fetch(`/consultations/${consultationId}`)
    .then(res => res.json())
    .then(data => {
      if (data.error) {
        showToast(data.error);
        return;
      }
      const visitDate = data.visit_datetime ? new Date(data.visit_datetime).toLocaleString('en-CA') : 'N/A';
      const symptomTags = (data.symptoms_present || []).map(s => `<span style="display:inline-block;background:#edf2f7;padding:6px 10px;margin:4px;border-radius:8px;font-size:12px;">${s.name} (${s.code})</span>`).join('');
      const modal = document.createElement('div');
      modal.innerHTML = `
        <div style="position:fixed;inset:0;background:rgba(15,23,42,0.85);display:flex;align-items:center;justify-content:center;padding:20px;z-index:9999;">
          <div style="background:white;border-radius:20px;padding:30px;max-width:900px;width:100%;max-height:90vh;overflow-y:auto;box-shadow:0 30px 80px rgba(0,0,0,0.35);">
            <div style="display:flex;justify-content:space-between;align-items:center;gap:20px;">
              <h2 style="margin:0;color:#1a202c;">Consultation Details</h2>
              <button class="btn btn-danger" type="button" id="closeConsultationModal">✕ Close</button>
            </div>
            <div style="margin-top:20px;background:#f7fafc;border-radius:12px;padding:20px;">
              <p><strong>Visit Date:</strong> ${visitDate}</p>
              <p><strong>Doctor:</strong> ${data.doctor?.name || 'N/A'} (${data.doctor?.department || 'Department N/A'})</p>
              <p><strong>Patient:</strong> ${data.patient?.name || 'N/A'} (UHID: ${data.patient?.uhid || 'N/A'})</p>
            </div>
            <h3 style="margin:20px 0 10px;color:#2d3748;">Transcript</h3>
            <div style="background:#edf2f7;border-radius:12px;padding:15px;font-size:13px;line-height:1.6;max-height:180px;overflow:auto;">${(data.transcript || '').replace(/\n/g,'<br>')}</div>
            <h3 style="margin:20px 0 10px;color:#2d3748;">Symptoms</h3>
            <div>${symptomTags || '<em>No symptoms recorded.</em>'}</div>
            <h3 style="margin:20px 0 10px;color:#2d3748;">Summary</h3>
            <div style="background:#1a202c;color:#f7fafc;border-radius:12px;padding:20px;font-family:monospace;font-size:13px;white-space:pre-wrap;max-height:220px;overflow:auto;">${data.summary || 'No summary available.'}</div>
          </div>
        </div>`;
      modal.id = 'consultationModal';
      document.body.appendChild(modal);
      modal.addEventListener('click', (evt) => {
        if (evt.target.id === 'consultationModal' || evt.target.id === 'closeConsultationModal') {
          modal.remove();
        }
      });
    })
    .catch(err => showToast(`Failed to load consultation: ${err.message}`));
}

function setupPatientPage() {
  const form = document.getElementById('patientForm');
  if (!form) return;
  resetPatientFormFields(form);

  const visitField = form.visitDateTime;
  if (visitField) {
    visitField.value = new Date().toLocaleString('en-CA');
    setInterval(() => visitField.value = new Date().toLocaleString('en-CA'), 1000);
  }

  const searchBtn = document.getElementById('searchBtn');
  if (searchBtn) {
    searchBtn.addEventListener('click', searchPatientByUhid);
  }

  const resetBtn = document.getElementById('resetRegistration');
  if (resetBtn) {
    resetBtn.addEventListener('click', () => {
      resetPatientFormFields(form);
    });
  }

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    await savePatientFromForm(form);
  });
}

async function searchPatientByUhid() {
  const uhidField = document.getElementById('searchUhid');
  const searchResult = document.getElementById('searchResult');
  if (!uhidField || !searchResult) return;

  const uhid = uhidField.value.trim();
  if (!uhid) {
    return showToast('Please enter a UHID to search.');
  }

  searchResult.innerHTML = '<p style="color:#4a5568">Searching...</p>';
  try {
    const res = await fetch(`/patients/${uhid}`);
    const data = await res.json();
    if (data.error) {
      searchResult.innerHTML = `<p style="color:#e53e3e">${data.error}</p>`;
      return;
    }

    searchResult.innerHTML = `
      <div class="info-banner">
        <span>✅</span>
        <div>
          <strong>${data.patient.name}</strong><br>
          UHID: ${data.patient.uhid} • Consultations: ${data.total_consultations}
        </div>
      </div>`;

    populatePatientForm(document.getElementById('patientForm'), data.patient);
    displayConsultationHistory(data.consultations || []);
    setPatientInfo(data.patient);
  } catch (err) {
    searchResult.innerHTML = `<p style="color:#e53e3e">Search failed: ${err.message}</p>`;
  }
}

async function savePatientFromForm(form) {
  const payload = {
    name: form.patientName.value.trim(),
    sex: form.sex.value,
    age: form.age.value,
    dob: form.dob.value,
    phone: form.phone.value.trim(),
    email: form.email.value.trim(),
    unit_suite: form.unitSuite.value.trim(),
    street: form.street.value.trim(),
    city: form.city.value.trim(),
    province: form.province.value,
    postal_code: form.postalCode.value.trim(),
    address: [form.unitSuite.value.trim(), form.street.value.trim(), form.city.value.trim(), form.province.value, form.postalCode.value.trim()].filter(Boolean).join(', ')
  };

  if (!payload.name || !payload.sex || !payload.age || !payload.dob || !payload.street || !payload.city || !payload.province || !payload.postal_code) {
    showToast('Please complete all required patient fields.');
    return;
  }

  try {
    const saved = await persistPatient(payload, existingPatientUhid);
    existingPatientUhid = saved.uhid;
    updateAutoUhidDisplay(existingPatientUhid);
    const enrichedPatient = {
      ...saved,
      visitDateTime: form.visitDateTime?.value || new Date().toLocaleString('en-CA'),
      unitSuite: payload.unit_suite,
      postalCode: payload.postal_code,
      address: payload.address,
      province: payload.province,
      city: payload.city,
      street: payload.street,
    };
    setPatientInfo(enrichedPatient);
    showToast('Patient saved successfully.');
    window.location.href = 'consultation.html';
  } catch (err) {
    showToast(`Patient save failed: ${err.message}`);
  }
}

// ---------------------- Consultation Page ----------------------
function setupConsultationPage() {
  const transcriptField = document.getElementById('transcript');
  if (!transcriptField) return;

  const startBtn = document.getElementById('startBtn');
  const stopBtn = document.getElementById('stopBtn');
  const fileInput = document.getElementById('fileInput');
  if (startBtn) startBtn.addEventListener('click', startRecording);
  if (stopBtn) stopBtn.addEventListener('click', stopRecording);
  if (fileInput) fileInput.addEventListener('change', handleAudioUpload);

  const summaryBtn = document.getElementById('generateSummaryBtn');
  if (summaryBtn) summaryBtn.addEventListener('click', generateSummary);

  const goSummaryBtn = document.getElementById('goToSummaryBtn');
  if (goSummaryBtn) goSummaryBtn.addEventListener('click', () => window.location.href = 'summary.html');
}

async function startRecording() {
  if (mediaRecorder && mediaRecorder.state === 'recording') {
    return;
  }

  toggleRecordingState(true);
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    mediaRecorder = new MediaRecorder(stream);
    recordedChunks = [];
    mediaRecorder.ondataavailable = event => recordedChunks.push(event.data);
    mediaRecorder.onstop = uploadRecording;
    mediaRecorder.start();
  } catch (err) {
    toggleRecordingState(false);
    showToast('Microphone access denied: ' + err.message);
  }
}

function stopRecording() {
  if (mediaRecorder && mediaRecorder.state !== 'inactive') {
    toggleRecordingState('processing');
    mediaRecorder.stop();
  }
}

function toggleRecordingState(state) {
  const startBtn = document.getElementById('startBtn');
  const stopBtn = document.getElementById('stopBtn');
  const uploadBtn = document.getElementById('uploadAudioBtn');
  const status = document.getElementById('statusBadge');

  const stateConfig = {
    idle: {
      disableStart: false,
      disableStop: true,
      disableUpload: false,
      statusText: 'Idle',
      statusClass: 'idle'
    },
    recording: {
      disableStart: true,
      disableStop: false,
      disableUpload: true,
      statusText: '🔴 Recording...',
      statusClass: 'recording'
    },
    processing: {
      disableStart: true,
      disableStop: true,
      disableUpload: true,
      statusText: '⏳ Processing...',
      statusClass: 'processing'
    }
  };

  const normalizedState = state === true ? 'recording' : state === false ? 'idle' : state;
  const config = stateConfig[normalizedState] || stateConfig.idle;

  const applyDisabledState = (btn, shouldDisable) => {
    if (!btn) return;
    btn.disabled = shouldDisable;
    btn.classList.toggle('is-disabled', shouldDisable);
  };

  applyDisabledState(startBtn, config.disableStart);
  applyDisabledState(uploadBtn, config.disableUpload);
  applyDisabledState(stopBtn, config.disableStop);

  if (status) {
    status.textContent = config.statusText;
    status.className = `status ${config.statusClass}`;
  }
}

async function uploadRecording() {
  if (!recordedChunks.length) {
    toggleRecordingState(false);
    return;
  }
  const blob = new Blob(recordedChunks, { type: 'audio/webm' });
  const fd = new FormData();
  fd.append('file', blob, 'recording.webm');
  try {
    const res = await fetch('/upload', { method: 'POST', body: fd });
    const data = await res.json();
    if (data.error) {
      showToast(data.error);
      return;
    }
    const transcriptField = document.getElementById('transcript');
    if (transcriptField) transcriptField.value = data.transcript || '';
  } catch (err) {
    showToast('Upload failed: ' + err.message);
  } finally {
    toggleRecordingState(false);
  }
}

async function handleAudioUpload(event) {
  const file = event.target.files[0];
  if (!file) return;
  const fd = new FormData();
  fd.append('file', file);
  try {
    const res = await fetch('/upload', { method: 'POST', body: fd });
    const data = await res.json();
    if (data.error) return showToast(data.error);
    const transcriptField = document.getElementById('transcript');
    if (transcriptField) transcriptField.value = data.transcript || '';
  } catch (err) {
    showToast('Upload failed: ' + err.message);
  }
}

async function generateSummary() {
  const transcript = document.getElementById('transcript')?.value;
  if (!transcript) {
    return showToast('Transcript is empty.');
  }
  const doctor = getDoctorInfo();
  const patient = getPatientInfo();
  if (!doctor || !patient) {
    return showToast('Please complete doctor and patient steps first.');
  }

  const fd = new FormData();
  fd.append('transcript', transcript);
  fd.append('doctor_info', JSON.stringify(doctor));
  fd.append('patient_info', JSON.stringify(patient));
  fd.append('audio_path', '');

  try {
    const res = await fetch('/summarize', { method: 'POST', body: fd });
    const data = await res.json();
    if (data.error) {
      showToast(data.error);
      return;
    }
    renderSymptoms(data.symptoms_present, data.unknown_mentions);
    const finalSummary = composeSummaryWithContext(data.summary);
    const summaryOutput = document.getElementById('summaryOutput');
    if (summaryOutput) summaryOutput.textContent = finalSummary;
    setSummaryData(finalSummary);
    setSymptomsData({
      validated: data.symptoms_present,
      unknown: data.unknown_mentions
    });
    setConsultationId(data.consultation_id);
    showToast('Summary generated successfully.');
    window.location.href = 'summary.html';
  } catch (err) {
    showToast('Summary failed: ' + err.message);
  }
}

function renderSymptoms(validated, unknown) {
  const validList = document.getElementById('validatedSymptoms');
  const unknownList = document.getElementById('unknownSymptoms');
  const symptomsSection = document.getElementById('symptomsSection');
  if (!validList || !unknownList) return;

  validList.innerHTML = '';
  unknownList.innerHTML = '';

  if (symptomsSection) {
    symptomsSection.classList.remove('hidden');
  }

  if (validated && validated.length) {
    validated.forEach(item => {
      const li = document.createElement('li');
      li.innerHTML = `<span><strong>${item.name}</strong> (${item.code}) - ${item.category}</span>`;
      validList.appendChild(li);
    });
  } else {
    validList.innerHTML = '<li>No validated symptoms</li>';
  }

  if (unknown && unknown.length) {
    unknown.forEach(item => {
      const li = document.createElement('li');
      li.innerHTML = `<span>${item}</span>`;
      unknownList.appendChild(li);
    });
  } else {
    unknownList.innerHTML = '<li>No unknown mentions</li>';
  }
}

// ---------------------- Summary Page ----------------------
function setupSummaryPage() {
  const summaryContainer = document.getElementById('summaryOutput');
  if (!summaryContainer) return;
  const summary = getSummaryData();
  summaryContainer.textContent = summary || 'No summary generated yet.';

  const downloadBtn = document.getElementById('downloadSummaryBtn');
  if (downloadBtn) {
    downloadBtn.addEventListener('click', downloadSummary);
  }

  const symptoms = getSymptomsData();
  if (symptoms) {
    renderSymptoms(symptoms.validated || [], symptoms.unknown || []);
  }
}

function downloadSummary() {
  const summary = getSummaryData();
  if (!summary) {
    return showToast('Nothing to download yet.');
  }
  const patient = getPatientInfo() || {};
  const blob = new Blob([summary], { type: 'text/plain' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  const uhid = patient.uhid || 'UNKNOWN';
  link.download = `Medical_Summary_${uhid}_${new Date().toISOString().split('T')[0]}.txt`;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
}

// ---------------------- Bootstrapping ----------------------
document.addEventListener('DOMContentLoaded', () => {
  populateProgressNav();
  const step = document.body.dataset.step || 'doctor';
  setActiveNav(step);
  startHeaderClock();
  if (step === 'doctor') {
    setupDoctorPage();
  } else if (step === 'patient') {
    setupPatientPage();
  } else if (step === 'consultation') {
    setupConsultationPage();
  } else if (step === 'summary') {
    setupSummaryPage();
  }
});
