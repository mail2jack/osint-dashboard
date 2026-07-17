const {
    makeWASocket,
    useMultiFileAuthState,
    DisconnectReason,
    fetchLatestBaileysVersion,
} = require('@whiskeysockets/baileys');
const { Boom } = require('@hapi/boom');
const express = require('express');
const pino = require('pino');
const fs = require('fs');
const path = require('path');

const app = express();
app.use(express.json());

const AUTH_DIR = process.env.AUTH_DIR || path.join(__dirname, 'auth');
const PORT = parseInt(process.env.PORT || '3001', 10);
const LOG_LEVEL = process.env.LOG_LEVEL || 'info';
const PAIRING_PHONE = process.env.PAIRING_PHONE || '';

const logger = pino({
    level: LOG_LEVEL,
    transport: LOG_LEVEL === 'debug'
        ? { target: 'pino-pretty', options: { colorize: true } }
        : undefined,
});

let sock = null;
let sessionState = 'disconnected';
let currentQR = null;

fs.mkdirSync(AUTH_DIR, { recursive: true });

async function startSession() {
    const { version, isLatest } = await fetchLatestBaileysVersion();
    logger.info({ version, isLatest }, 'Using Baileys version');

    let auth;
    try {
        const result = await useMultiFileAuthState(AUTH_DIR);
        auth = result;
    } catch (e) {
        logger.error({ err: e }, 'Failed to load auth state');
        auth = { state: null, saveCreds: () => {} };
    }

    sock = makeWASocket({
        version,
        auth: auth.state,
        logger: pino({ level: LOG_LEVEL }),
        printQRInTerminal: false,
        browser: ['Iveras OSINT', 'Chrome', '126.0'],
        syncFullHistory: false,
        markOnlineOnConnect: false,
        generateHighQualityLinkPreview: false,
        shouldSyncHistoryMessage: false,
    });

    sock.ev.on('creds.update', auth.saveCreds);

    let pairingAttempted = false;

    sock.ev.on('connection.update', (update) => {
        const { connection, lastDisconnect, qr } = update;

        if (qr && !pairingAttempted) {
            currentQR = qr;
            sessionState = 'awaiting_qr';

            if (PAIRING_PHONE) {
                pairingAttempted = true;
                logger.info({ phone: PAIRING_PHONE }, 'Requesting pairing code...');
                sock.requestPairingCode(PAIRING_PHONE)
                    .then((code) => {
                        logger.info(`═══════════════════════════════════════════`);
                        logger.info(`  Pairing code: ${code}`);
                        logger.info(`  Enter this code on your phone:`);
                        logger.info(`  WhatsApp > Linked Devices > Link Device`);
                        logger.info(`═══════════════════════════════════════════`);
                        sessionState = 'pairing';
                    })
                    .catch((err) => {
                        logger.error({ err }, 'Pairing code request failed, fallback to QR');
                        pairingAttempted = false;
                    });
            } else {
                logger.info('────────── QR Code ──────────');
                logger.info('Scan from WhatsApp > Linked Devices');
                logger.info('GET /api/qr to retrieve');
            }
        }

        if (connection === 'open') {
            sessionState = 'connected';
            currentQR = null;
            logger.info('WhatsApp session connected');
        }

        if (connection === 'close') {
            const reason = lastDisconnect?.error;
            const statusCode = reason instanceof Boom
                ? reason.output.statusCode
                : null;
            const isLoggedOut = statusCode === DisconnectReason.loggedOut;
            const isBadSession = statusCode === DisconnectReason.badSession;

            if (isLoggedOut || isBadSession) {
                sessionState = 'disconnected';
                sock = null;
                logger.warn({ statusCode }, 'Session ended — re-auth required');
                try {
                    fs.rmSync(AUTH_DIR, { recursive: true, force: true });
                } catch (_) {}
                fs.mkdirSync(AUTH_DIR, { recursive: true });
            } else {
                sessionState = 'reconnecting';
                logger.info({ statusCode }, 'Connection closed — reconnecting in 5s');
                pairingAttempted = false;
                setTimeout(() => {
                    startSession().catch((err) => {
                        logger.error({ err }, 'Reconnect failed');
                    });
                }, 5000);
            }
        }
    });
}

async function checkPhoneNumber(phone) {
    if (!sock || sessionState !== 'connected') {
        return { error: 'WhatsApp session not connected', sessionState };
    }

    const result = { exists: false };

    try {
        const onwa = await sock.onWhatsApp(phone);
        const wa = onwa && onwa[0];

        if (wa && wa.exists) {
            result.exists = true;
            result.jid = wa.jid;

            try {
                result.profilePicUrl = await sock.profilePictureUrl(wa.jid, 'image');
            } catch (_) {}

            try {
                const status = await sock.fetchStatus(wa.jid);
                if (status && status.status) {
                    result.status = status.status;
                }
            } catch (_) {}

            try {
                const biz = await sock.getBusinessProfile(wa.jid);
                if (biz && (biz.description || biz.website || biz.email)) {
                    result.isBusiness = true;
                    result.businessProfile = {
                        description: biz.description || null,
                        website: Array.isArray(biz.website) ? biz.website : [],
                        email: biz.email || null,
                        category: biz.category || null,
                        address: biz.address || null,
                        businessHours: biz.business_hours || null,
                    };
                }
            } catch (_) {}

            try {
                const ppThumb = await sock.profilePictureUrl(wa.jid, 'preview');
                if (ppThumb && !result.profilePicUrl) {
                    result.profilePicUrl = ppThumb;
                }
            } catch (_) {}
        }
    } catch (e) {
        logger.error({ err: e, phone }, 'checkPhoneNumber failed');
        return { error: e.message, exists: false };
    }

    return result;
}

// --- REST endpoints ---

app.get('/health', (_req, res) => {
    res.json({ status: 'ok', session: sessionState });
});

app.get('/api/status', (_req, res) => {
    res.json({
        connected: sessionState === 'connected',
        state: sessionState,
    });
});

app.get('/api/qr', (_req, res) => {
    if (sessionState === 'connected') {
        return res.json({ connected: true, message: 'Already connected' });
    }
    if (!currentQR) {
        return res.json({ qr: null, message: 'No QR yet. Try again in a few seconds.' });
    }
    res.json({ qr: currentQR });
});

app.post('/api/pairing', async (req, res) => {
    const { phone } = req.body;
    if (!phone) {
        return res.status(400).json({ error: 'Phone number required' });
    }
    if (!sock) {
        return res.status(400).json({ error: 'Session not initialized' });
    }
    try {
        const code = await sock.requestPairingCode(phone);
        sessionState = 'pairing';
        res.json({ pairingCode: code });
    } catch (e) {
        res.status(500).json({ error: e.message });
    }
});

app.post('/api/restart', async (_req, res) => {
    if (sock) {
        sock.end(new Error('Manual restart'));
        sock = null;
    }
    sessionState = 'reconnecting';
    currentQR = null;
    fs.rmSync(AUTH_DIR, { recursive: true, force: true });
    fs.mkdirSync(AUTH_DIR, { recursive: true });
    startSession().catch(() => {});
    res.json({ message: 'Session restarted' });
});

app.post('/api/check', async (req, res) => {
    const { phone } = req.body;
    if (!phone) {
        return res.status(400).json({ error: 'Phone number required' });
    }
    const result = await checkPhoneNumber(phone);
    res.json(result);
});

app.listen(PORT, '0.0.0.0', () => {
    logger.info({ port: PORT, authDir: AUTH_DIR }, 'WA Service started');
    startSession().catch((err) => {
        logger.error({ err }, 'Failed to start session');
    });
});
