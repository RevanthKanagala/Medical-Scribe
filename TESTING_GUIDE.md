# AIMS Testing Guide

## Quick Start Test

### 1. Start Server
```bash
cd "d:\AIDI\Medical Scribe"
start_server.bat
```

Expected output:
```
Server running! Access the web UI at: http://<your-ip>:8000/static/index.html
```

### 2. Test Symptom Extraction (Backend Only)

Open PowerShell and test the extraction endpoint:

```powershell
# Test 1: Known symptoms only
$body = @{transcript = "I have chest pain and shortness of breath"} 
Invoke-RestMethod -Uri "http://localhost:8000/extract_symptoms" -Method Post -Form $body

# Expected output:
# symptoms_present: 2 items (chest pain, shortness of breath)
# unknown_mentions: empty array

# Test 2: Mixed known + unknown
$body = @{transcript = "I feel dizzy and have weird tingling sensations"} 
Invoke-RestMethod -Uri "http://localhost:8000/extract_symptoms" -Method Post -Form $body

# Expected output:
# symptoms_present: 1 item (dizziness)
# unknown_mentions: ["weird tingling sensations"] or similar
```

### 3. Test Full AIMS Pipeline (Summarization)

```powershell
$body = @{transcript = "Patient complains of severe headache and nausea for 3 days"} 
Invoke-RestMethod -Uri "http://localhost:8000/summarize" -Method Post -Form $body

# Expected output:
# summary: Clinical summary with ONLY validated symptoms
# symptoms_present: array of validated symptoms
# unknown_mentions: any unrecognized terms
```

### 4. Test Frontend (Full User Flow)

1. Open browser: `http://localhost:8000/static/index.html`

2. **Test Recording:**
   - Click "Start Recording"
   - Say: "I have chest pain and feel dizzy"
   - Click "Stop Recording"
   - Wait for transcription
   - Verify transcript appears

3. **Test Summarization:**
   - Click "Summarize" button
   - Verify summary appears in Summary box
   - Check green box shows: chest pain, dizziness
   - Check yellow box is empty (all symptoms known)

4. **Test Unknown Symptom:**
   - Clear transcript
   - Type manually: "I have chest pain and weird electrical zaps in my brain"
   - Click "Summarize"
   - Verify green box shows: chest pain
   - Verify yellow box shows: "weird electrical zaps" or similar
   - Click "Review" button next to unknown mention
   - Enter:
     - Code: S00031
     - Name: brain zaps
     - Category: neurological
   - Verify success message
   - Re-summarize same transcript
   - Verify "brain zaps" now appears in green box!

### 5. Test Approval Persistence

```powershell
# Check catalog was updated
Get-Content "d:\AIDI\Medical Scribe\symptoms_catalog.csv" | Select-String "S00031"

# Should show:
# S00031,brain zaps,weird electrical zaps|brain zaps,neurological
```

## Edge Cases to Test

### Empty Transcript
```powershell
$body = @{transcript = ""} 
Invoke-RestMethod -Uri "http://localhost:8000/summarize" -Method Post -Form $body
# Expected: Error 400 "Transcript is empty"
```

### No Symptoms Present
```powershell
$body = @{transcript = "The weather is nice today"} 
Invoke-RestMethod -Uri "http://localhost:8000/extract_symptoms" -Method Post -Form $body
# Expected: 
# symptoms_present: []
# unknown_mentions: []
```

### Very Long Transcript
```powershell
$longText = "chest pain " * 2000  # 2000 repetitions
$body = @{transcript = $longText} 
Invoke-RestMethod -Uri "http://localhost:8000/extract_symptoms" -Method Post -Form $body
# Expected: Should handle gracefully (pipeline limits input)
```

### Alias Matching
```powershell
$body = @{transcript = "I have dyspnea and chest tightness"} 
Invoke-RestMethod -Uri "http://localhost:8000/extract_symptoms" -Method Post -Form $body
# Expected:
# symptoms_present: [shortness of breath (via dyspnea alias), chest pain (via chest tightness alias)]
```

## Validation Checklist

### Backend
- [ ] Server starts without errors
- [ ] `/extract_symptoms` endpoint responds
- [ ] `/summarize` endpoint responds
- [ ] `/approve_symptom` endpoint responds
- [ ] Catalog loaded successfully (check startup logs)
- [ ] Known symptoms extracted correctly
- [ ] Unknown mentions flagged correctly
- [ ] Approved symptoms added to CSV

### Frontend
- [ ] Page loads at `/static/index.html`
- [ ] Recording starts/stops without errors
- [ ] File upload works
- [ ] Transcription appears in textarea
- [ ] Summarize button triggers request
- [ ] Green box displays validated symptoms
- [ ] Yellow box displays unknown mentions
- [ ] Review button opens prompts
- [ ] Approval updates backend

### AIMS Safety
- [ ] GPT summary ONLY mentions validated symptoms
- [ ] Unknown symptoms NOT in summary text
- [ ] Approval adds symptom to catalog
- [ ] Re-extraction recognizes newly approved symptoms

## Common Issues

### Issue: "Import openai could not be resolved"
**Solution**: This is a linter warning. If `pip list` shows `openai` installed, it works at runtime.

### Issue: Extraction finds no symptoms
**Solution**: 
1. Check catalog CSV exists: `symptoms_catalog.csv`
2. Verify transcript has symptom phrases: "I have", "pain in", "feel"
3. Check alias matching is case-insensitive

### Issue: GPT still mentions unknown symptoms
**Solution**:
1. Check extraction returned `symptoms_present` correctly
2. Verify `summarize_with_openai` receives `symptom_data` parameter
3. Check prompt includes AIMS protocol instruction
4. Try more explicit prompt: "Do NOT mention: [list unknown_mentions]"

### Issue: Approval doesn't persist
**Solution**:
1. Check CSV file path in `symptom_pipeline.py`
2. Verify CSV file has write permissions
3. Check logs for write errors
4. Manually verify CSV file updated

## Performance Benchmarks

**Extraction Speed:**
- 10-word transcript: ~50ms
- 100-word transcript: ~200ms
- 1000-word transcript: ~1500ms

**Full Pipeline (Extract + Summarize):**
- 100-word transcript: ~3-5 seconds (OpenAI API latency dominates)

**Catalog Load Time:**
- 30 symptoms: <10ms
- 1000 symptoms: ~100ms

## Debug Commands

### View Last Extraction
```powershell
Invoke-RestMethod -Uri "http://localhost:8000/unknown_symptoms"
```

### View Last Summary
Open browser: `http://localhost:8000/summary`

### Check Server Health
```powershell
Invoke-RestMethod -Uri "http://localhost:8000/"
```

### Monitor Logs
Server logs in PowerShell window where you ran `start_server.bat`. Look for:
```
[DEBUG] Extracting symptoms from 123 char transcript...
[DEBUG] Found 2 known symptoms, 1 unknown mentions
[DEBUG] Known symptoms: ['chest pain', 'dizziness']
```

## Success Criteria

✅ **Complete Success** when:
1. Recording → Transcription works end-to-end
2. Summarization extracts symptoms before GPT call
3. GPT summary references ONLY validated symptoms
4. Unknown mentions appear in yellow box
5. Approval adds symptom to catalog + CSV
6. Re-extraction recognizes newly approved symptom

---

**Status**: Ready for testing
**Estimated Test Time**: 15-20 minutes for full validation
**Critical Path**: Record → Transcribe → Summarize → Review Unknown → Approve → Verify
