# FCC Informal Complaint Submission Guide

## T-Mobile Home Internet Service Quality Issues

This guide walks you through the process of filing an FCC informal complaint against T-Mobile for failing to deliver advertised internet service speeds. Use this in conjunction with the NetPulse Dashboard's automated evidence collection and report generation tools.

---

## Table of Contents

1. [Before You File](#before-you-file)
2. [Understanding the FCC Complaint Process](#understanding-the-fcc-complaint-process)
3. [Gathering Required Information](#gathering-required-information)
4. [Preparing Your Evidence Package](#preparing-your-evidence-package)
5. [Filing the Complaint](#filing-the-complaint)
6. [Writing Your Complaint Narrative](#writing-your-complaint-narrative)
7. [What Happens After You File](#what-happens-after-you-file)
8. [Escalation Options](#escalation-options)
9. [Tips for Success](#tips-for-success)

---

## Before You File

### Minimum Recommended Evidence Collection

Before filing an FCC complaint, ensure you have collected sufficient evidence to demonstrate a pattern of service issues. The dashboard tracks your readiness via the `/api/fcc-readiness` endpoint.

**Recommended minimums:**
- At least 50 speed tests (100+ is better)
- Data collection spanning at least 7 days (30+ days is ideal)
- At least one documented support interaction with T-Mobile
- Signal quality metrics showing acceptable signal during poor performance

**Check your readiness:**
```
GET http://localhost:8080/api/fcc-readiness
```

### Contact T-Mobile First

The FCC expects you to have attempted to resolve the issue with T-Mobile directly before filing. Document all interactions:

- Call T-Mobile support: 1-800-T-MOBILE (1-800-866-2453)
- Use T-Mobile app or website chat
- Visit a T-Mobile store
- Send a written complaint to T-Mobile's executive team

**Important:** Log all support interactions in the dashboard's Support Interactions section. This becomes evidence for your complaint.

---

## Understanding the FCC Complaint Process

### Informal vs. Formal Complaints

| Aspect | Informal Complaint | Formal Complaint |
|--------|-------------------|------------------|
| **Cost** | Free | $625 filing fee |
| **Process** | Online form | Legal proceeding |
| **Response Time** | 30 days required response | Varies, FCC adjudicates |
| **Resolution** | Company-mediated | FCC-ordered |
| **Recommended For** | Start here | If informal fails |

**Start with an informal complaint.** T-Mobile is required to respond within 30 days, and the FCC tracks complaint patterns against carriers.

### What the FCC Can Do

- Require T-Mobile to respond to your complaint
- Track complaint patterns (high complaint volume triggers investigations)
- Investigate systematic service issues
- Fine carriers for violations of transparency rules
- Order remediation for deceptive advertising

### What the FCC Cannot Do

- Force T-Mobile to improve your specific connection
- Award monetary damages (use small claims court for that)
- Guarantee a specific outcome
- Make T-Mobile honor speeds they never contractually guaranteed

---

## Gathering Required Information

### Account Information (Required)

You will need:

- **Account holder full name** (as it appears on the bill)
- **Service address** (where the gateway is installed)
- **T-Mobile account number** (found on bill or in T-Mobile app)
- **Plan name** (e.g., "T-Mobile 5G Home Internet - Rely Plan")
- **Monthly cost** (what you pay, not including taxes)
- **Service start date** (when you signed up)

### Contact Information (Required)

- Your email address
- Your phone number
- Mailing address

### Problem Description (Required)

- Issue category: Internet
- Sub-category: Speed/Service Quality
- Detailed description of the problem
- Dates when the problem occurs
- What resolution you want

---

## Preparing Your Evidence Package

The NetPulse Dashboard generates comprehensive evidence reports. Use these API endpoints to generate your evidence:

### Generate FCC Complaint Report

```
# Full JSON report
GET http://localhost:8080/api/fcc-report?format=json&days=30

# CSV export (for raw data attachment)
GET http://localhost:8080/api/fcc-report?format=csv&days=30

# PDF report (formatted narrative)
GET http://localhost:8080/api/fcc-report?format=pdf&days=30
```

### Evidence Components

Your evidence package should include:

#### 1. Speed Test Data (Required)

Export from the dashboard showing:
- Date and time of each test
- Download speed achieved
- Upload speed achieved
- Latency/ping measurements
- Server used for testing

**Key metrics to highlight:**
- Average download speed vs. advertised minimum
- Percentage of tests below 10 Mbps
- Percentage of advertised minimum actually received

#### 2. Signal Quality Analysis (Important)

This proves the issue is NOT your equipment or location:
- 5G SINR (Signal-to-Noise Ratio) - should be >10 dB for "good"
- 5G RSRP (Reference Signal Received Power) - should be >-100 dBm
- Correlation showing good signal + poor speeds = congestion

The dashboard's signal analysis proves: "Signal quality is acceptable, therefore poor speeds indicate network congestion or deprioritization, not a coverage issue."

#### 3. Time-of-Day Analysis (Important)

Demonstrates congestion patterns:
- Speeds at 2-5 AM vs. peak hours (6 PM - 10 PM)
- Shows performance varies with network load, not signal
- Proves T-Mobile's network is oversubscribed

#### 4. Support Interaction Log (Important)

Document every T-Mobile contact:
- Date and time of contact
- Method (phone, chat, store, email)
- Agent name or ID if available
- Your complaint summary
- T-Mobile's response
- Resolution status

**Especially document any dismissive responses** like:
- "Speed tests don't matter"
- "If you can stream video, service is working"
- "Speeds are not guaranteed"

#### 5. Advertised vs. Actual Comparison (Required)

From T-Mobile's own disclosures:
- Rely Plan: 133-415 Mbps download "typical"
- Source: https://www.t-mobile.com/home-internet/plans
- Your actual average: [from dashboard]
- Percentage of minimum delivered: [from dashboard]

#### 6. Screenshots (Recommended)

- T-Mobile's speed estimates for your address
- Your plan details from T-Mobile account
- Speed test results
- Dashboard showing performance metrics

---

## Filing the Complaint

### Step 1: Go to the FCC Consumer Complaint Center

**URL:** https://consumercomplaints.fcc.gov

### Step 2: Select Your Issue Type

1. Click "File a Complaint"
2. Select "Internet"
3. Select "Speed" or "Service Quality"
4. Select your state

### Step 3: Enter Provider Information

- Provider Name: **T-Mobile**
- Service Type: **Home Internet / Fixed Wireless**
- Account Number: [Your T-Mobile account number]

### Step 4: Describe the Problem

Use the complaint narrative template below. Be factual, specific, and include data.

### Step 5: Attach Evidence

Upload:
- PDF report from dashboard (primary evidence)
- CSV data export (raw data backup)
- Screenshots of advertised speeds
- Any relevant support interaction transcripts

**File size limit:** 10 MB per file, 20 files maximum

### Step 6: Review and Submit

- Verify all information is accurate
- Confirm your contact information
- Submit the complaint

You will receive a confirmation number. **Save this.**

---

## Writing Your Complaint Narrative

### Template

```
SUBJECT: T-Mobile Home Internet - Consistent Failure to Deliver Advertised Speeds

I have been a T-Mobile Home Internet subscriber since [START DATE], paying
$[MONTHLY COST]/month for [PLAN NAME] service at [SERVICE ADDRESS]. T-Mobile
advertises typical download speeds of 133-415 Mbps for my service address.

DOCUMENTED PERFORMANCE:
Over a period of [X] days, I conducted [Y] automated speed tests using the
Ookla Speedtest service. My documented average download speed is [AVG] Mbps,
which is only [Z]% of the minimum advertised typical speed. [W]% of my speed
tests recorded download speeds below 10 Mbps, which is insufficient for basic
internet usage.

SIGNAL QUALITY ANALYSIS:
My 5G gateway consistently shows acceptable signal quality metrics:
- Average SINR: [X] dB (threshold for "good" is >10 dB)
- Average RSRP: [X] dBm (threshold for "fair" is >-100 dBm)

These metrics indicate my equipment has adequate signal reception. The poor
speeds despite acceptable signal demonstrate that this is a network capacity
or congestion issue, not a coverage or equipment problem.

TIME-OF-DAY PATTERN:
Speed tests show a clear congestion pattern. During off-peak hours (2-5 AM),
I record speeds of [X] Mbps. During peak evening hours (6-10 PM), speeds drop
to [Y] Mbps. This [Z]x improvement during off-peak hours confirms the tower
serving my area is oversubscribed.

SUPPORT INTERACTIONS:
I have contacted T-Mobile support [X] time(s) regarding this issue:
[List dates, methods, and responses received]

Despite multiple contacts, T-Mobile has not resolved the performance issues
or offered any meaningful remedy.

RESOLUTION REQUESTED:
I am seeking:
[ ] Service improvement to deliver advertised speeds
[ ] Billing credit/refund for months of substandard service
[ ] Release from service without penalty
[ ] Other: [specify]

ATTACHED EVIDENCE:
1. FCC Complaint Report (PDF) - Comprehensive performance analysis
2. Speed Test Data (CSV) - Raw speed test results with timestamps
3. Screenshots - Advertised speeds for my address
4. Support Interaction Log - Documentation of T-Mobile contacts
```

### Key Points to Emphasize

1. **Be specific with numbers.** "7.2 Mbps average" is better than "very slow."

2. **Reference T-Mobile's own disclosures.** Quote their advertised speeds.

3. **Show you've tried to resolve it.** Document support contacts.

4. **Prove it's not your fault.** Signal quality data shows the issue is network-side.

5. **State what you want.** Be clear about your desired resolution.

---

## What Happens After You File

### Timeline

| Day | What Happens |
|-----|--------------|
| 0 | You file complaint |
| 1-3 | FCC sends complaint to T-Mobile |
| 1-30 | T-Mobile must respond |
| 30 | Deadline for T-Mobile response |
| 30+ | FCC reviews if you're unsatisfied |

### T-Mobile's Response

T-Mobile's executive response team will contact you. They typically:
- Call or email within 2-5 business days
- Review your account and complaint
- Offer a resolution (credit, service improvement, or explanation)

**Be prepared to:**
- Explain your issue again
- Provide additional evidence if requested
- Negotiate if the initial offer is insufficient

### If You're Unsatisfied

After T-Mobile responds, you can:
1. Reply to the FCC indicating you're not satisfied
2. The FCC may request additional information
3. Consider escalation options (see next section)

---

## Escalation Options

### If Informal Complaint Fails

1. **File a Formal FCC Complaint** ($625 fee)
   - More rigorous legal process
   - FCC adjudicates the dispute

2. **File with State Attorney General**
   - Many states have consumer protection divisions
   - Search: "[Your State] Attorney General Consumer Complaint"

3. **File with State Public Utilities Commission**
   - Some states regulate ISPs more strictly
   - Varies by state

4. **Small Claims Court**
   - Sue for monetary damages
   - Dollar limits vary by state ($5,000-$25,000 typically)
   - No lawyer needed
   - T-Mobile must send representative

5. **Class Action Investigation**
   - Report to consumer advocacy organizations
   - Document your case for potential class action
   - National Consumer Law Center
   - Electronic Frontier Foundation

### Additional Resources

- **FCC Speed Test App:** https://www.fcc.gov/consumers/guides/fcc-speed-test-app
- **FCC Broadband Labels:** https://www.fcc.gov/broadband-labels
- **Measuring Broadband America:** https://www.fcc.gov/general/measuring-broadband-america

---

## Tips for Success

### Do

- **Collect data consistently.** Run tests at the same times daily for at least 30 days.
- **Use multiple test servers.** Eliminates server-side variability.
- **Document everything.** Screenshots, transcripts, dates, times.
- **Be factual and professional.** Emotions don't help; data does.
- **Include signal metrics.** This is your proof that poor speeds aren't your fault.
- **Reference official sources.** Quote T-Mobile's own advertised speeds.
- **State clear resolution requests.** What do you actually want?

### Don't

- **Exaggerate.** Stick to documented facts.
- **Make it personal.** Focus on the service, not individuals.
- **Omit your own troubleshooting.** Show you've tried to fix it.
- **Wait too long.** File while the issue is ongoing and documented.
- **Expect guaranteed results.** FCC complaints influence patterns, not individual cases.

### Strengthening Your Case

The more data you have, the stronger your case:

| Evidence Strength | Data Volume |
|------------------|-------------|
| Weak | <50 tests, <7 days |
| Moderate | 50-100 tests, 7-14 days |
| Strong | 100-200 tests, 14-30 days |
| Very Strong | 200+ tests, 30+ days |

Include tests from different:
- Times of day (morning, afternoon, evening, night)
- Days of week (weekdays vs. weekends)
- Conditions (good weather vs. bad)

---

## Dashboard Integration

The NetPulse Dashboard automates evidence collection and report generation:

### API Endpoints

| Endpoint | Purpose |
|----------|---------|
| `GET /api/fcc-readiness` | Check if you have enough data to file |
| `GET /api/fcc-report?format=json` | Full complaint report in JSON |
| `GET /api/fcc-report?format=csv` | Raw data export for attachment |
| `GET /api/fcc-report?format=pdf` | Formatted PDF report |
| `GET /api/service-terms` | Your documented service terms |
| `GET /api/support-interactions` | Support interaction log |
| `GET /api/diagnostic-report` | Technical diagnostic report |
| `GET /api/speedtest-analysis` | Speed vs. signal correlation |

### Web Interface

Navigate to the dashboard's "FCC Complaint" tab to:
- View readiness status
- Generate and download reports
- Review and edit support interaction logs
- Preview complaint narrative

---

## Legal Disclaimer

This guide is for informational purposes only and does not constitute legal advice. The FCC complaint process is a consumer remedy, not a legal proceeding. For legal advice regarding your specific situation, consult with a licensed attorney.

---

## Quick Reference Card

**FCC Consumer Complaint Center:**
https://consumercomplaints.fcc.gov

**T-Mobile Support:**
1-800-T-MOBILE (1-800-866-2453)

**T-Mobile Executive Response Team:**
executiveresponse@t-mobile.com

**T-Mobile Network Management Disclosure:**
https://www.t-mobile.com/home-internet/policies/internet-service/network-management-practices

**Your Service Plan Details:**
- Plan: T-Mobile 5G Home Internet - Rely Plan
- Advertised Download: 133-415 Mbps (typical)
- Advertised Upload: 12-55 Mbps (typical)
- Price: $50/month (subject to change)
- Deprioritization Threshold: 1.2 TB/month

---

*This guide was generated as part of the NetPulse (Home Internet Monitoring) project for FCC complaint preparation.*
