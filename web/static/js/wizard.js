'use strict';

const STORAGE_KEYS = {
  doctor: 'aims_doctor',
  patient: 'aims_patient',
  summary: 'aims_summary',
  symptoms: 'aims_symptoms',
  consultationId: 'aims_consultation_id',
  validation: 'aims_validation',
  transcript: 'aims_transcript',
  audioPath: 'aims_audio_path',
  processingState: 'aims_processing_state'
};

let mediaRecorder;
let recordedChunks = [];
let recordedMimeType = 'audio/webm';
let recordingStartedAt = 0;
let monitorStream;
let monitorAudioContext;
let monitorAnalyser;
let monitorAnimationFrame;
let existingPatientUhid = '';

const navSteps = [
  { id: 'doctor', label: 'Doctor Info', href: 'doctor.html' },
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

function getSupportedRecordingMimeType() {
  if (typeof MediaRecorder === 'undefined' || typeof MediaRecorder.isTypeSupported !== 'function') {
    return '';
  }

  const candidates = [
    'audio/webm;codecs=opus',
    'audio/webm',
    'audio/ogg;codecs=opus',
    'audio/mp4'
  ];

  return candidates.find(type => MediaRecorder.isTypeSupported(type)) || '';
}

function getSelectedAudioDeviceId() {
  return document.getElementById('audioInputSelect')?.value || '';
}

function getAudioConstraints() {
  const deviceId = getSelectedAudioDeviceId();
  if (deviceId) {
    return {
      deviceId: { exact: deviceId },
      channelCount: 1
    };
  }
  return true;
}

function updateMicMeter(level = 0, label = 'Microphone level unavailable') {
  const meterFill = document.getElementById('micMeterFill');
  const meterLabel = document.getElementById('micMeterLabel');
  if (meterFill) meterFill.style.width = `${Math.max(0, Math.min(100, level))}%`;
  if (meterLabel) meterLabel.textContent = label;
}

function stopMicMonitor() {
  if (monitorAnimationFrame) {
    cancelAnimationFrame(monitorAnimationFrame);
    monitorAnimationFrame = null;
  }
  if (monitorStream) {
    monitorStream.getTracks().forEach(track => track.stop());
    monitorStream = null;
  }
  if (monitorAudioContext) {
    monitorAudioContext.close().catch(() => {});
    monitorAudioContext = null;
  }
  monitorAnalyser = null;
  updateMicMeter(0, 'Microphone level unavailable');
}

function startMicMonitor(stream) {
  stopMicMonitor();
  monitorStream = stream;
  monitorAudioContext = new (window.AudioContext || window.webkitAudioContext)();
  const source = monitorAudioContext.createMediaStreamSource(stream);
  monitorAnalyser = monitorAudioContext.createAnalyser();
  monitorAnalyser.fftSize = 2048;
  source.connect(monitorAnalyser);
  const buffer = new Uint8Array(monitorAnalyser.fftSize);

  const tick = () => {
    if (!monitorAnalyser) return;
    monitorAnalyser.getByteTimeDomainData(buffer);
    let sumSquares = 0;
    for (let i = 0; i < buffer.length; i += 1) {
      const centered = (buffer[i] - 128) / 128;
      sumSquares += centered * centered;
    }
    const rms = Math.sqrt(sumSquares / buffer.length);
    const level = Math.min(100, Math.round(rms * 280));
    let label = 'Microphone active, but input is very low';
    if (level > 35) {
      label = 'Microphone input looks healthy';
    } else if (level > 12) {
      label = 'Microphone input detected';
    }
    updateMicMeter(level, label);
    monitorAnimationFrame = requestAnimationFrame(tick);
  };

  tick();
}

async function populateAudioInputs(requestPermission = false) {
  let permissionStream = null;
  try {
    if (requestPermission) {
      permissionStream = await navigator.mediaDevices.getUserMedia({ audio: true });
    }

    const select = document.getElementById('audioInputSelect');
    if (!select || !navigator.mediaDevices?.enumerateDevices) {
      return;
    }

    const currentValue = select.value;
    const devices = await navigator.mediaDevices.enumerateDevices();
    const audioInputs = devices.filter(device => device.kind === 'audioinput');

    select.innerHTML = '';
    const defaultOption = document.createElement('option');
    defaultOption.value = '';
    defaultOption.textContent = 'Default microphone';
    select.appendChild(defaultOption);

    audioInputs.forEach((device, index) => {
      const option = document.createElement('option');
      option.value = device.deviceId;
      option.textContent = device.label || `Microphone ${index + 1}`;
      select.appendChild(option);
    });

    if ([...select.options].some(option => option.value === currentValue)) {
      select.value = currentValue;
    }
  } catch (err) {
    showToast(`Unable to access microphone list: ${err.message}`);
  } finally {
    if (permissionStream) {
      permissionStream.getTracks().forEach(track => track.stop());
    }
  }
}

async function refreshMicMonitor() {
  if (!navigator.mediaDevices?.getUserMedia) {
    updateMicMeter(0, 'Microphone monitoring is not supported in this browser');
    return;
  }

  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: getAudioConstraints() });
    startMicMonitor(stream);
  } catch (err) {
    updateMicMeter(0, 'Unable to read from the selected microphone');
  }
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

function setTranscriptData(text) {
  saveToStorage(STORAGE_KEYS.transcript, text || '');
}

function getTranscriptData() {
  return readFromStorage(STORAGE_KEYS.transcript) || '';
}

function setValidationData(data) {
  saveToStorage(STORAGE_KEYS.validation, data);
}

function getValidationData() {
  return readFromStorage(STORAGE_KEYS.validation);
}

function setConsultationId(id) {
  saveToStorage(STORAGE_KEYS.consultationId, id);
}

function getConsultationId() {
  return readFromStorage(STORAGE_KEYS.consultationId);
}

function setAudioPath(path) {
  saveToStorage(STORAGE_KEYS.audioPath, path || '');
}

function getAudioPath() {
  return readFromStorage(STORAGE_KEYS.audioPath) || '';
}

function setProcessingState(state) {
  saveToStorage(STORAGE_KEYS.processingState, state || null);
}

function getProcessingState() {
  return readFromStorage(STORAGE_KEYS.processingState);
}

function clearProcessingState() {
  localStorage.removeItem(STORAGE_KEYS.processingState);
}

function clearGeneratedConsultationArtifacts() {
  localStorage.removeItem(STORAGE_KEYS.summary);
  localStorage.removeItem(STORAGE_KEYS.symptoms);
  localStorage.removeItem(STORAGE_KEYS.validation);
  localStorage.removeItem(STORAGE_KEYS.consultationId);
  clearProcessingState();
}

function showToast(message) {
  alert(message);
}

function isLowInformationTranscript(text) {
  const normalized = (text || '').trim().toLowerCase().replace(/\s+/g, ' ');
  if (!normalized) return true;

  const meaninglessPhrases = new Set([
    'you',
    'you you',
    'you you you',
    'thank you',
    'thanks',
    'ok',
    'okay'
  ]);
  if (meaninglessPhrases.has(normalized)) return true;

  const tokens = normalized.split(' ').filter(Boolean);
  if (tokens.length <= 3 && new Set(tokens).size === 1) return true;
  return normalized.length < 12;
}

function escapeHtml(value) {
  return String(value || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/\"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function escapeRegex(value) {
  return String(value || '').replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
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

function createDeferredSummary(message, audioPath) {
  const doctor = getDoctorInfo() || {};
  const patient = getPatientInfo() || {};
  const deferredMessage = message || 'Processing is deferred until the service issue is resolved.';
  const summary = [
    '============================================================================',
    'MEDICAL CONSULTATION STATUS',
    '============================================================================',
    '',
    'Status: Deferred processing',
    `Doctor: ${doctor.name || 'N/A'}`,
    `Patient: ${patient.name || 'N/A'}`,
    `UHID: ${patient.uhid || 'Pending'}`,
    `Visit: ${patient.visitDateTime || new Date().toLocaleString('en-CA')}`,
    '',
    'The consultation audio has been saved successfully.',
    'Transcript, symptom extraction, and summary generation will continue once the issue is resolved.',
    audioPath ? `Saved audio file: ${audioPath}` : '',
    `Current issue: ${deferredMessage}`,
    '',
    'Presentation note: recording continued without interruption.',
    '============================================================================'
  ].filter(Boolean).join('\n');
  return composeSummaryWithContext(summary);
}

function activateDeferredProcessing(options = {}) {
  const state = {
    processing_deferred: true,
    stage: options.stage || 'processing',
    message: options.message || 'Processing is deferred until the issue is resolved.',
    audio_path: options.audioPath || getAudioPath() || '',
    created_at: new Date().toISOString()
  };

  if (state.audio_path) {
    setAudioPath(state.audio_path);
  }

  setProcessingState(state);
  setSummaryData(createDeferredSummary(state.message, state.audio_path));
  setSymptomsData({ validated: [], unknown: [], summaryItems: [] });
  setValidationData(null);
  setConsultationId(null);
  return state;
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
  renderFollowUpQuestions();
  setPatientInfo(null);
  setValidationData(null);
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

function renderFollowUpQuestions(questions = [], latestVisitIso) {
  const card = document.getElementById('followUpCard');
  const list = document.getElementById('followUpList');
  const meta = document.getElementById('followUpMeta');
  if (!card || !list || !meta) return;

  list.innerHTML = '';
  if (!questions || questions.length === 0) {
    card.classList.add('hidden');
    meta.textContent = 'Last visit: --';
    return;
  }

  const formattedVisit = latestVisitIso ? new Date(latestVisitIso).toLocaleString('en-CA') : '--';
  meta.textContent = `Last visit: ${formattedVisit}`;
  card.classList.remove('hidden');

  questions.forEach((question, index) => {
    const li = document.createElement('li');
    li.textContent = question;
    li.setAttribute('data-question-index', String(index + 1));
    list.appendChild(li);
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
    const latestVisit = data.consultations?.[0]?.visit_datetime;
    const questionSet = data.follow_up_questions?.length ? data.follow_up_questions : data.consultations?.[0]?.follow_up_questions;
    renderFollowUpQuestions(questionSet || [], latestVisit);
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
  const savedTranscript = getTranscriptData();
  if (savedTranscript && !transcriptField.value) {
    transcriptField.value = savedTranscript;
  }

  const startBtn = document.getElementById('startBtn');
  const stopBtn = document.getElementById('stopBtn');
  const fileInput = document.getElementById('fileInput');
  const micSelect = document.getElementById('audioInputSelect');
  const refreshMicBtn = document.getElementById('refreshMicBtn');
  if (startBtn) startBtn.addEventListener('click', startRecording);
  if (stopBtn) stopBtn.addEventListener('click', stopRecording);
  if (fileInput) fileInput.addEventListener('change', handleAudioUpload);
  if (micSelect) micSelect.addEventListener('change', refreshMicMonitor);
  if (refreshMicBtn) refreshMicBtn.addEventListener('click', async () => {
    await populateAudioInputs(true);
    await refreshMicMonitor();
  });

  const summaryBtn = document.getElementById('generateSummaryBtn');
  if (summaryBtn) summaryBtn.addEventListener('click', generateSummary);

  const goSummaryBtn = document.getElementById('goToSummaryBtn');
  if (goSummaryBtn) goSummaryBtn.addEventListener('click', () => window.location.href = 'summary.html');

  populateAudioInputs();
}

async function startRecording() {
  if (mediaRecorder && mediaRecorder.state === 'recording') {
    return;
  }

  if (typeof MediaRecorder === 'undefined') {
    showToast('This browser does not support audio recording.');
    return;
  }

  toggleRecordingState(true);
  try {
    stopMicMonitor();
    const stream = await navigator.mediaDevices.getUserMedia({ audio: getAudioConstraints() });
    await populateAudioInputs(true);
    const mimeType = getSupportedRecordingMimeType();
    const options = mimeType ? { mimeType, audioBitsPerSecond: 128000 } : undefined;
    mediaRecorder = options ? new MediaRecorder(stream, options) : new MediaRecorder(stream);
    recordedChunks = [];
    recordedMimeType = mediaRecorder.mimeType || mimeType || 'audio/webm';
    recordingStartedAt = Date.now();
    mediaRecorder.ondataavailable = event => {
      if (event.data && event.data.size > 0) {
        recordedChunks.push(event.data);
      }
    };
    mediaRecorder.onstop = async () => {
      stream.getTracks().forEach(track => track.stop());
      await uploadRecording();
      await refreshMicMonitor();
    };
    mediaRecorder.start(1000);
  } catch (err) {
    toggleRecordingState(false);
    await refreshMicMonitor();
    showToast('Microphone access denied: ' + err.message);
  }
}

function stopRecording() {
  if (mediaRecorder && mediaRecorder.state !== 'inactive') {
    toggleRecordingState('processing');
    try {
      mediaRecorder.requestData();
    } catch (err) {
    }
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
  const durationMs = recordingStartedAt ? Date.now() - recordingStartedAt : 0;
  if (!recordedChunks.length || durationMs < 1000) {
    recordedChunks = [];
    toggleRecordingState(false);
    showToast('Recording was too short. Please speak for at least a second and try again.');
    return;
  }
  const blobType = recordedMimeType || 'audio/webm';
  const extension = blobType.includes('ogg') ? 'ogg' : blobType.includes('mp4') ? 'mp4' : 'webm';
  const blob = new Blob(recordedChunks, { type: blobType });
  const fd = new FormData();
  fd.append('file', blob, `recording.${extension}`);
  try {
    clearGeneratedConsultationArtifacts();
    const res = await fetch('/upload', { method: 'POST', body: fd });
    const data = await res.json();
    if (data.error) {
      clearGeneratedConsultationArtifacts();
      showToast(data.error);
      return;
    }
    if (data.audio_path) {
      setAudioPath(data.audio_path);
    }
    if (data.processing_deferred) {
      const transcriptField = document.getElementById('transcript');
      if (transcriptField) transcriptField.value = '';
      setTranscriptData('');
      activateDeferredProcessing({
        stage: data.stage,
        message: data.message,
        audioPath: data.audio_path
      });
      showToast(data.message || 'Audio saved. Processing is deferred until the issue is fixed.');
      return;
    }
    if (!data.transcript || !data.transcript.trim()) {
      clearGeneratedConsultationArtifacts();
      showToast('No speech was detected. Try a longer recording and verify the active microphone.');
      return;
    }
    const transcriptField = document.getElementById('transcript');
    if (transcriptField) transcriptField.value = data.transcript || '';
    setTranscriptData(data.transcript || '');
    clearProcessingState();
  } catch (err) {
    clearGeneratedConsultationArtifacts();
    showToast('Upload failed: ' + err.message);
  } finally {
    recordedChunks = [];
    recordingStartedAt = 0;
    toggleRecordingState(false);
  }
}

async function handleAudioUpload(event) {
  const file = event.target.files[0];
  if (!file) return;
  const fd = new FormData();
  fd.append('file', file);
  try {
    clearGeneratedConsultationArtifacts();
    const res = await fetch('/upload', { method: 'POST', body: fd });
    const data = await res.json();
    if (data.error) return showToast(data.error);
    if (data.audio_path) {
      setAudioPath(data.audio_path);
    }
    if (data.processing_deferred) {
      const transcriptField = document.getElementById('transcript');
      if (transcriptField) transcriptField.value = '';
      setTranscriptData('');
      activateDeferredProcessing({
        stage: data.stage,
        message: data.message,
        audioPath: data.audio_path
      });
      return showToast(data.message || 'Audio saved. Processing is deferred until the issue is fixed.');
    }
    if (!data.transcript || !data.transcript.trim()) {
      clearGeneratedConsultationArtifacts();
      return showToast('No speech was detected in the uploaded file.');
    }
    const transcriptField = document.getElementById('transcript');
    if (transcriptField) transcriptField.value = data.transcript || '';
    setTranscriptData(data.transcript || '');
    clearProcessingState();
  } catch (err) {
    showToast('Upload failed: ' + err.message);
  }
}

async function generateSummary() {
  const transcript = document.getElementById('transcript')?.value;
  const audioPath = getAudioPath();
  const existingProcessingState = getProcessingState();
  if (!transcript) {
    if (audioPath || (existingProcessingState && existingProcessingState.processing_deferred)) {
      activateDeferredProcessing({
        stage: 'transcription',
        message: 'Transcript is not available yet. The saved audio can be processed after the issue is resolved.',
        audioPath
      });
      window.location.href = 'summary.html';
      return;
    }
    clearGeneratedConsultationArtifacts();
    return showToast('Transcript is empty.');
  }
  if (isLowInformationTranscript(transcript)) {
    clearGeneratedConsultationArtifacts();
    return showToast('Transcript quality is too low to summarize. Re-record with the correct microphone and clear speech.');
  }
  setTranscriptData(transcript);
  const doctor = getDoctorInfo();
  const patient = getPatientInfo();
  if (!doctor || !patient) {
    return showToast('Please complete doctor and patient steps first.');
  }

  const fd = new FormData();
  fd.append('transcript', transcript);
  fd.append('doctor_info', JSON.stringify(doctor));
  fd.append('patient_info', JSON.stringify(patient));
  fd.append('audio_path', audioPath || '');

  try {
    const res = await fetch('/summarize', { method: 'POST', body: fd });
    const data = await res.json();
    if (data.error) {
      activateDeferredProcessing({
        stage: 'summary',
        message: data.error,
        audioPath
      });
      showToast('Summary service is unavailable. Showing deferred processing state instead.');
      window.location.href = 'summary.html';
      return;
    }
    if (data.processing_deferred) {
      activateDeferredProcessing({
        stage: data.stage,
        message: data.message,
        audioPath: data.audio_path || audioPath
      });
      window.location.href = 'summary.html';
      return;
    }
    renderSymptoms(data.symptoms_present, data.unknown_mentions);
    const finalSummary = composeSummaryWithContext(data.summary);
    const summaryOutput = document.getElementById('summaryOutput');
    if (summaryOutput) summaryOutput.textContent = finalSummary;
    setSummaryData(finalSummary);
    setTranscriptData(transcript);
    setSymptomsData({
      validated: data.symptoms_present,
      unknown: data.unknown_mentions,
      summaryItems: (data.summary_validation && data.summary_validation.summary_items) || []
    });
    setValidationData(data.summary_validation || null);
    setConsultationId(data.consultation_id);
    clearProcessingState();
    showToast('Summary generated successfully.');
    window.location.href = 'summary.html';
  } catch (err) {
    activateDeferredProcessing({
      stage: 'summary',
      message: `Summary generation failed. ${err.message}`,
      audioPath
    });
    showToast('Summary service is unavailable. Showing deferred processing state instead.');
    window.location.href = 'summary.html';
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
      li.className = 'symptom-clickable';
      li.title = 'Click to find this symptom in the transcript';
      li.innerHTML = `<span><strong>${item.name}</strong> (${item.code}) — ${item.category}</span><span class="symptom-link-hint">🔍 Find in transcript</span>`;
      li.addEventListener('click', () => handleSymptomClick(item.name, item.matched_text));
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

function renderSummarySymptomLinks(summaryItems) {
  if (!summaryItems || !summaryItems.length) {
    return '<section><h4>Symptoms extracted from summary</h4><p>No summary symptoms were extracted.</p></section>';
  }

  const listItems = summaryItems.map(item => {
    const label = item.matched_symptom_name
      ? `${item.summary_text} -> ${item.matched_symptom_name}`
      : item.summary_text;
    return `<li><span>${label}</span></li>`;
  }).join('');

  return `<section><h4>Symptoms extracted from summary</h4><ul class="summary-symptom-links">${listItems}</ul></section>`;
}

function renderValidationResult(validation) {
  const card = document.getElementById('validationCard');
  const statusEl = document.getElementById('validationStatus');
  const detailsEl = document.getElementById('validationDetails');
  if (!card || !statusEl || !detailsEl) return;

  if (!validation) {
    card.classList.add('hidden');
    statusEl.textContent = 'No validation data available.';
    detailsEl.innerHTML = '';
    return;
  }

  card.classList.remove('hidden');
  const isValid = Boolean(validation.is_valid);
  statusEl.textContent = isValid
    ? 'Summary symptoms match the validated transcript findings.'
    : 'Review required: summary symptoms differ from transcript extraction.';
  statusEl.className = `validation-status ${isValid ? 'status-ok' : 'status-warn'}`;

  let detailsHtml = '';
  detailsHtml += renderSummarySymptomLinks(validation.summary_items || []);
  if (validation.missing_symptoms && validation.missing_symptoms.length) {
    const list = validation.missing_symptoms.map(item => `<li>${item}</li>`).join('');
    detailsHtml += `<section><h4>Symptoms not represented in summary</h4><ul>${list}</ul></section>`;
  }
  if (validation.unmatched_summary_items && validation.unmatched_summary_items.length) {
    const list = validation.unmatched_summary_items.map(item => `<li>${item}</li>`).join('');
    detailsHtml += `<section><h4>Summary items not in validated catalog</h4><ul>${list}</ul></section>`;
  }

  if (!detailsHtml) {
    detailsHtml = '<p>All validated symptoms are reflected in the summary.</p>';
  }
  detailsEl.innerHTML = detailsHtml;
}

function highlightTermInTranscript(transcript, term) {
  if (!transcript || !term) {
    return { html: escapeHtml(transcript).replace(/\n/g, '<br>'), matches: 0 };
  }

  const regex = new RegExp(escapeRegex(term), 'gi');
  let lastIndex = 0;
  let result = '';
  let matches = 0;
  let match;

  while ((match = regex.exec(transcript)) !== null) {
    result += escapeHtml(transcript.slice(lastIndex, match.index));
    matches += 1;
    const className = matches === 1 ? 'symptom-highlight first-match' : 'symptom-highlight';
    result += `<mark class="${className}">${escapeHtml(match[0])}</mark>`;
    lastIndex = regex.lastIndex;
  }

  result += escapeHtml(transcript.slice(lastIndex));
  return { html: result.replace(/\n/g, '<br>'), matches };
}

function buildTranscriptSearchTerms(symptomName, matchedText) {
  const baseTerms = [matchedText, symptomName]
    .map(value => String(value || '').trim().toLowerCase())
    .filter(Boolean);

  const modifiers = new Set([
    'sharp', 'severe', 'mild', 'moderate', 'chronic', 'acute', 'persistent',
    'really', 'bad', 'extreme', 'significant', 'localized', 'generalized'
  ]);

  const expandedTerms = [];
  baseTerms.forEach(term => {
    expandedTerms.push(term);

    const words = term.split(/\s+/).filter(Boolean);
    const strippedWords = words.filter(word => !modifiers.has(word));
    if (strippedWords.length && strippedWords.join(' ') !== term) {
      expandedTerms.push(strippedWords.join(' '));
    }

    if (words.length >= 2) {
      for (let size = words.length - 1; size >= 2; size -= 1) {
        for (let start = 0; start <= words.length - size; start += 1) {
          expandedTerms.push(words.slice(start, start + size).join(' '));
        }
      }
    }
  });

  return [...new Set(expandedTerms)].sort((a, b) => b.length - a.length);
}

function findBestTranscriptMatch(transcript, symptomName, matchedText) {
  const terms = buildTranscriptSearchTerms(symptomName, matchedText);
  for (const term of terms) {
    const regex = new RegExp(escapeRegex(term), 'i');
    if (regex.test(transcript)) {
      return term;
    }
  }
  return null;
}

function focusTermInConsultationTextarea(term) {
  const transcriptField = document.getElementById('transcript');
  if (!transcriptField) return false;
  const text = transcriptField.value || '';
  const lowerText = text.toLowerCase();
  const lowerTerm = String(term || '').toLowerCase();
  const index = lowerText.indexOf(lowerTerm);
  if (index === -1) return false;

  transcriptField.focus();
  transcriptField.setSelectionRange(index, index + lowerTerm.length);
  const lineBefore = text.slice(0, index).split('\n').length;
  transcriptField.scrollTop = Math.max(0, (lineBefore - 2) * 20);
  return true;
}

function handleSymptomClick(symptomName, matchedText) {
  const transcript = getTranscriptData() || document.getElementById('transcript')?.value || '';
  if (!transcript) {
    showToast('Transcript not available yet.');
    return;
  }

  const bestMatch = findBestTranscriptMatch(transcript, symptomName, matchedText);

  const transcriptPanel = document.getElementById('transcriptPanel');
  const transcriptView = document.getElementById('transcriptView');
  const transcriptLabel = document.getElementById('transcriptHighlightLabel');

  if (transcriptPanel && transcriptView && transcriptLabel) {
    const { html, matches } = highlightTermInTranscript(transcript, bestMatch || symptomName);
    transcriptPanel.classList.remove('hidden');
    transcriptView.innerHTML = html;

    if (matches > 0) {
      const label = bestMatch && bestMatch.toLowerCase() !== String(symptomName || '').toLowerCase()
        ? `Showing ${matches} match(es) for "${bestMatch}" linked from "${symptomName}".`
        : `Showing ${matches} match(es) for "${symptomName}" in transcript.`;
      transcriptLabel.textContent = label;
      const first = transcriptView.querySelector('mark.first-match');
      if (first) {
        first.scrollIntoView({ behavior: 'smooth', block: 'center' });
      }
    } else {
      transcriptLabel.textContent = `No direct match found for "${symptomName}" in transcript.`;
    }
    return;
  }

  if (!focusTermInConsultationTextarea(bestMatch || symptomName)) {
    showToast(`Could not find "${symptomName}" in transcript.`);
  }
}

// ---------------------- Summary Page ----------------------
function setupSummaryPage() {
  const summaryContainer = document.getElementById('summaryOutput');
  if (!summaryContainer) return;
  const summary = getSummaryData();
  summaryContainer.textContent = summary || 'No summary generated yet.';

  const processingBanner = document.getElementById('processingBanner');
  const processingState = getProcessingState();
  if (processingBanner) {
    if (processingState && processingState.processing_deferred) {
      processingBanner.classList.remove('hidden');
      processingBanner.innerHTML = `<span>⏳</span><div><strong>Deferred processing</strong><br>${escapeHtml(processingState.message || 'Processing will resume after the issue is resolved.')}</div>`;
    } else {
      processingBanner.classList.add('hidden');
      processingBanner.innerHTML = '';
    }
  }

  const downloadBtn = document.getElementById('downloadPdfBtn') || document.getElementById('downloadSummaryBtn');
  if (downloadBtn) {
    downloadBtn.addEventListener('click', downloadSummary);
  }

  const symptoms = getSymptomsData();
  if (symptoms) {
    renderSymptoms(symptoms.validated || [], symptoms.unknown || []);
  }

  renderValidationResult(getValidationData());
}

function downloadSummary() {
  const summary = getSummaryData();
  if (!summary) {
    return showToast('Nothing to download yet.');
  }

  const jsPdfNamespace = window.jspdf;
  if (!jsPdfNamespace || !jsPdfNamespace.jsPDF) {
    showToast('PDF library is unavailable. Please refresh and try again.');
    return;
  }

  const patient = getPatientInfo() || {};
  const doctor = getDoctorInfo() || {};
  const transcript = getTranscriptData() || '';
  const uhid = patient.uhid || 'UNKNOWN';

  const { jsPDF } = jsPdfNamespace;
  const doc = new jsPDF({ unit: 'pt', format: 'a4' });
  const pageWidth = doc.internal.pageSize.getWidth();
  const pageHeight = doc.internal.pageSize.getHeight();
  const margin = 42;
  const textWidth = pageWidth - margin * 2;
  let y = margin;

  const ensureSpace = (needed = 18) => {
    if (y + needed > pageHeight - margin) {
      doc.addPage();
      y = margin;
    }
  };

  const writeHeading = (text) => {
    ensureSpace(28);
    doc.setFont('helvetica', 'bold');
    doc.setFontSize(16);
    doc.text(text, margin, y);
    y += 24;
  };

  const writeLabelValue = (label, value) => {
    const line = `${label}: ${value || 'N/A'}`;
    const lines = doc.splitTextToSize(line, textWidth);
    doc.setFont('helvetica', 'normal');
    doc.setFontSize(11);
    lines.forEach(part => {
      ensureSpace(16);
      doc.text(part, margin, y);
      y += 15;
    });
  };

  const writeBlock = (title, content) => {
    ensureSpace(24);
    doc.setFont('helvetica', 'bold');
    doc.setFontSize(12);
    doc.text(title, margin, y);
    y += 16;

    const lines = doc.splitTextToSize(content || 'N/A', textWidth);
    doc.setFont('helvetica', 'normal');
    doc.setFontSize(10);
    lines.forEach(part => {
      ensureSpace(14);
      doc.text(part, margin, y);
      y += 13;
    });
    y += 8;
  };

  const generatedAt = new Date().toLocaleString('en-CA');
  writeHeading('Medical Consultation Summary');
  writeLabelValue('Generated', generatedAt);
  writeLabelValue('Patient UHID', uhid);
  writeLabelValue('Patient Name', patient.name || 'N/A');
  writeLabelValue('Doctor', doctor.name || 'N/A');
  writeLabelValue('Department', doctor.department || 'N/A');
  writeLabelValue('Designation', doctor.designation || 'N/A');
  y += 8;

  writeBlock('Summary', summary);
  if (transcript) {
    writeBlock('Transcript', transcript);
  }

  doc.save(`Medical_Summary_${uhid}_${new Date().toISOString().split('T')[0]}.pdf`);
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
