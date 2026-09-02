# System Architecture: Custom Email Tracking Gateway & SMTP Relay

This document outlines the architectural blueprint, data flow, and implementation requirements for an in-house application capable of tracking email opens, link clicks, and spam/delivery metrics for emails sent outside the organization.

---

## 1. System Architecture Overview

The system operates as an intermediary **Email API Gateway / Custom SMTP Relay** that intercepts outgoing messages, injects tracking infrastructure into the HTML payload, and monitors feedback loops from external mail servers.

```
[In-House App] ---> [Tracking Gateway] ---> [External Mail Servers] ---> [Recipient Inbox]
                          ^                             |
                          | (Webhook / Callback)        | (DSN / Feedback Loop)
                          +-----------------------------+
```

---

## 2. Core Tracking Mechanisms

### A. Open Tracking (The Tracking Pixel)
To detect if an email is read without requiring explicit user action, the gateway injects a transparent, hidden 1x1 pixel image at the bottom of the HTML body.

*   **Injected HTML:**
    ```html
    <img src="https://click.yourdomain.com/track/open?id=msg_unique_id" width="1" height="1" style="display:none !important;" alt="" />
    ```
*   **Workflow:**
    1. The recipient opens the email client.
    2. The client fetches the 1x1 image from your tracking domain.
    3. The gateway logs the request timestamp against `msg_unique_id` in the database.
*   **Limitations:** Highly dependent on the recipient client's remote image loading policy. Apple Mail Privacy Protection (MPP) may trigger false positives by pre-fetching images.

### B. Click Tracking (Link Wrapping)
To track link clicks, the gateway parses the HTML body and wraps all outbound anchor (`<a>`) tags through a tracking redirect endpoint.

*   **Transformation Matrix:**
    | Original URL | Rewritten Tracking URL |
    | :--- | :--- |
    | `https://targetsite.com/page` | `https://click.yourdomain.com/track/click?id=msg_unique_id&dest=aHR0cHM6Ly90YXJnZXRzaXRlLmNvbS9wYWdl` |
*   **Workflow:**
    1. The user clicks the link.
    2. The request hits your gateway route `/track/click`.
    3. The gateway records the click event, timestamp, and message ID.
    4. The gateway issues a **302 Redirect** immediately to the Base64-decoded destination URL (`dest`).

### C. Spam & Delivery Tracking (Feedback Loop Integration)
External servers do not report back if an email lands in the spam folder silently. The system monitors delivery status through two technical proxies:

1.  **Asynchronous Delivery Status Notifications (DSN):**
    *   The gateway processes inbound SMTP bounce messages.
    *   If an external firewall blocks the email due to reputation, it rejects it with an explicit SMTP code (e.g., `554 5.7.1 Message rejected as spam`).
2.  **Feedback Loops (FBL):**
    *   The domain must be registered with major providers (Gmail, Yahoo, Microsoft).
    *   When a user clicks "Report Spam", the provider sends an automated report in **Abuse Reporting Format (ARF)** back to your system.

---

## 3. Database Schema Blueprint (PostgreSQL / Redis)

To track the lifecycle of each message, maintain a persistent transactional state:

```sql
CREATE TABLE email_tracking (
    message_id VARCHAR(64) PRIMARY KEY,
    sender_email VARCHAR(255) NOT NULL,
    recipient_email VARCHAR(255) NOT NULL,
    subject VARCHAR(255),
    status VARCHAR(50) DEFAULT 'sent', -- sent, delivered, opened, clicked, bounced, spam
    sent_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    delivered_at TIMESTAMP WITH TIME ZONE,
    opened_at TIMESTAMP WITH TIME ZONE,
    last_clicked_at TIMESTAMP WITH TIME ZONE
);

CREATE TABLE click_logs (
    click_id SERIAL PRIMARY KEY,
    message_id VARCHAR(64) REFERENCES email_tracking(message_id),
    destination_url TEXT NOT NULL,
    clicked_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    ip_address VARCHAR(45),
    user_agent TEXT
);
```

---

## 4. Implementation Strategies

### Strategy A: Hybrid Infrastructure (Recommended)
Leverage a cloud API provider (e.g., **AWS SES**, **Twilio SendGrid**) as your physical delivery engine while your application acts as the control plane.
*   **Pros:** High deliverability, automated IP warming, built-in webhook delivery pipelines.
*   **How it works:** Your application passes the email metadata to the Cloud API. You configure a Webhook listener on your application to receive event payloads (`event: open`, `event: click`, `event: bounce`, `event: spam_complaint`).

### Strategy B: Full Self-Hosted Engine
Build a dedicated SMTP listener and tracking controller using **Node.js (Nodemailer/SmtpServer)** or **Python (FastAPI/Aiosmtpd)**.
*   **Pros:** Total data sovereignty, no external API costs.
*   **Cons:** Heavy operational overhead maintaining domain reputation, dealing with IP blacklists, and parsing raw multi-part MIME emails.

---

## 5. Critical Infrastructure Checklist
Before launching this gateway outside the organization, your IT Infrastructure team must configure the following domain validation policies to prevent your tracking scripts from classifying your mail as phishing/spam:

*   [ ] **SPF (Sender Policy Framework):** Authorize your specific gateway server IPs to send mail.
*   [ ] **DKIM (DomainKeys Identified Mail):** Cryptographically sign all outgoing payloads.
*   [ ] **DMARC (Domain-based Message Authentication):** Enforce strict `p=reject` or `p=quarantine` alignment.
*   [ ] **Custom Tracking Domain:** Set up a CNAME record mapping your tracking URL (`click.yourdomain.com`) directly to your gateway application, ensuring SSL certificates match your core enterprise root domain.
