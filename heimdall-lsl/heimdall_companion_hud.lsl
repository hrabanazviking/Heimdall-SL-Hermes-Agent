// ============================================================================
// Heimdall Companion HUD — Gjallarhorn Companion (single-script build)
// Phase 3 slice 3. Design authority:
//   * doc 03 §7.6 — companion scripts as a sixth sense organ
//   * doc 03 §2.3 / §3 — the exact LSL calls used below
//   * doc 07 §5 — the companion design (sense-organ + relay, never a brain)
//   * doc 05 — safety invariants (scripted-agent disclosure is build law)
//
// WHAT THIS SCRIPT IS
//   A worn sense organ. On a slow timer it samples the wearer's immediate
//   situation with the §7.6 call set, compresses it into ONE compact
//   HUD1 digest line, and relays it off-world to the Heimdall endpoint
//   with llHTTPRequest. It never speaks publicly as the avatar, never
//   originates RLV, never makes decisions. Brains live out-world.
//
// HONEST STATUS: this script has NOT been worn or tested in-world.
//   Phase 1 (real grid login) is blocked — Secure Vault credentials are
//   unavailable — so no in-world compile/wear test was possible for
//   this slice. Treat the // VERIFY notes below as the pre-flight list
//   for the first in-world session.
//
// WIRE FORMAT (HUD1) — the single source of truth is README-hud.md in
//   this folder; heimdall-api/heimdall/awareness/hud_ingest.py mirrors
//   it exactly. One line, pipe-separated key=value fields, fixed order:
//
//   HUD1|v=1|ts=<unix>|region=<esc>|fps=<f>|dil=<f>|agents=<i>|
//   apos=<vec>;<vec>;...|self_pos=<vec>|self_flags=<i>|
//   env_day_len=<i>|env_day_off=<i>|env_sec=<f>|parcel=<esc>|
//   parcel_area=<i>|parcel_flags=<i>|parcel_id=<key>|
//   sensor_count=<i>|sensor_pos=<vec>;<vec>;...|free_mem=<i>
//   [|auth=<shared-secret>]        (only when CONFIG_SECRET is set)
//
//   <vec> is the LSL (string)vector form "<x, y, z>".
//   <esc> is llEscapeURL() text (region / parcel names).
//   apos holds AT MOST MAX_CENSUS_POSITIONS positions while agents=
//   holds the full (<=100) census count: keys from llGetAgentList are
//   reduced to anonymous positions HERE, in-world — no agent key and
//   no agent name ever leaves the region in a HUD1 digest. Out-world
//   these positions may only ever feed the COARSE presence tier
//   (doc 03 §2.1/§7.7: anonymous census, never named people).
//
// MEMORY DISCIPLINE (LSL Mono, 64 KB per script — doc 07 §1.3)
//   * No accumulation: every list is local to one digest cycle and is
//     explicitly freed (assigned []) before the HTTP call.
//   * The agent list is the dangerous one (keys are the most expensive
//     list elements, ~102 B each): it is reduced to a count + capped
//     positions immediately, never stored in a global.
//   * Sensor results are capped by LSL itself at 16 nearest.
//   * The digest string is built once per cycle; with the position
//     caps above it stays well under ~1.5 KB. Nothing is logged or
//     queued in-script; a failed relay is dropped, not retried from a
//     growing queue (doc 07 §5.5 degradation ladder).
//
// THROTTLE DISCIPLINE (doc 07 §3.2)
//   llHTTPRequest: 25 requests / 20 s per object, 1000 / 20 s per
//   owner; a throttled call returns NULL_KEY and calling again extends
//   the throttle (~40 s of silence to recover). DIGEST_INTERVAL_S is
//   60 s — one request per minute, far inside every budget. If a call
//   does return NULL_KEY we simply wait for the next tick.
// ============================================================================

// ---- CONFIG (edit these, nothing else needs touching) ----------------------
// Off-world Heimdall endpoint that receives HUD1 POSTs. Ships EMPTY on
// purpose: no real URL and no secret belongs in this repository. While
// empty the HUD senses, keeps its disclosure text up, and reports via
// llOwnerSay that relay is unconfigured — it never guesses an endpoint.
string  CONFIG_ENDPOINT_URL = "";
// Optional shared secret, carried IN THE BODY as the auth= field
// (doc 07 §2.2/§5.3: the simulator's x-secondlife-object-key header
// corroborates origin; the body-carried secret is the shared proof).
// Ships EMPTY. Set it out-of-band to the same value the ingest side
// is configured with; never commit a real value here.
string  CONFIG_SECRET = "";
// Digest cadence. 60 s >> the llHTTPRequest throttle floor (doc 07 §5.4
// heartbeat cadence is also ~60 s).
float   DIGEST_INTERVAL_S = 60.0;
// Sensor working radius (llSensor hard max is 96 m, doc 07 §2.5).
float   SENSOR_RANGE_M = 32.0;
// Cap on census positions carried in one digest (count is uncapped up
// to llGetAgentList's own 100-agent ceiling).
integer MAX_CENSUS_POSITIONS = 32;

// Format tag shared with the Python parser. The test-suite asserts the
// parser's expected tag string appears verbatim in this file.
string  FORMAT_TAG = "HUD1";
string  BUILD_VERSION = "heimdall-companion-hud/0.1 (phase3-slice3)";

// Disclosure (doc 05): the HUD is the in-world scripted-agent
// transparency anchor. This exact text is the hover text and the
// touch reply — it is never optional and never removed at runtime.
string  DISCLOSURE_TEXT =
    "Scripted Agent — Heimdall Companion\n" +
    "This avatar is operated by a scripted agent.\n" +
    "The wearer is a bot (Heimdall mind-bridge).";

// ---- Per-cycle scratch (globals only because sensor/http are async) ------
// These hold ONE in-flight digest cycle and are overwritten wholesale
// every cycle; nothing accumulates across cycles.
string  g_region = "";
float   g_fps = 0.0;
float   g_dil = 0.0;
integer g_agents = 0;
string  g_apos = "";
vector  g_self_pos = ZERO_VECTOR;
integer g_self_flags = 0;
integer g_env_day_len = 0;
integer g_env_day_off = 0;
float   g_env_sec = 0.0;
string  g_parcel = "";
integer g_parcel_area = 0;
integer g_parcel_flags = 0;
string  g_parcel_id = "";
key     g_http_id = NULL_KEY;
integer g_announced_unconfigured = FALSE;

set_disclosure()
{
    // Hover text: always on while worn (transparency anchor, doc 05).
    llSetText(DISCLOSURE_TEXT, <1.0, 1.0, 1.0>, 1.0);
}

string vec_field(vector v)
{
    // (string)vector yields "<x, y, z>" — exactly the <vec> form the
    // HUD1 spec defines; the parser strips the brackets.
    return (string)v;
}

gather_sync_fields()
{
    key owner = llGetOwner();
    g_self_pos = llGetPos();
    g_region = llGetRegionName();

    // Region health pair (doc 03 §3.5): simulator FPS (nominal ~45)
    // and time dilation (1.0 = realtime, lower = sim under load).
    g_fps = llGetRegionFPS();
    g_dil = llGetRegionTimeDilation();

    // Wearer's own canonical state flags (doc 03 §2.3 bitfield:
    // AGENT_FLYING / AGENT_SITTING / AGENT_TYPING / AGENT_AWAY /
    // AGENT_AUTOMATED / ...). One integer on the wire; decoding is
    // out-world work.
    g_self_flags = llGetAgentInfo(owner);

    // Virtual time of day (doc 03 §3.4): llGetEnvironment at the
    // wearer's position, ENVIRONMENT_DAYINFO only.
    // VERIFY: return order [integer day_length, integer day_offset,
    // float secs_since_midnight] is per the LSL reference for
    // ENVIRONMENT_DAYINFO (value 200); confirm on first in-world run.
    list env = llGetEnvironment(g_self_pos, [ENVIRONMENT_DAYINFO]);
    if (llGetListLength(env) >= 3)
    {
        g_env_day_len = llList2Integer(env, 0);
        g_env_day_off = llList2Integer(env, 1);
        g_env_sec     = llList2Float(env, 2);
    }
    else
    {
        g_env_day_len = 0;
        g_env_day_off = 0;
        g_env_sec     = 0.0;
    }
    env = []; // free before the expensive list below

    // Parcel at the wearer's position (doc 03 §3.2): name + area + id
    // via llGetParcelDetails, flags via llGetParcelFlags.
    list pd = llGetParcelDetails(g_self_pos,
        [PARCEL_DETAILS_NAME, PARCEL_DETAILS_AREA, PARCEL_DETAILS_ID]);
    if (llGetListLength(pd) >= 3)
    {
        g_parcel      = llList2String(pd, 0);
        g_parcel_area = llList2Integer(pd, 1);
        g_parcel_id   = (string)llList2Key(pd, 2);
    }
    else
    {
        g_parcel = "";
        g_parcel_area = 0;
        g_parcel_id = (string)NULL_KEY;
    }
    pd = [];
    g_parcel_flags = llGetParcelFlags(g_self_pos);

    // Region census (doc 03 §2.3): llGetAgentList, REGION scope,
    // returns up to 100 agent keys or a [string error] list.
    // Signature llGetAgentList(integer scope, list options) per the
    // current LSL reference; doc 03's older (max_agents, flags) form
    // was the pre-release signature — do not "fix" this back.
    list agents = llGetAgentList(AGENT_LIST_REGION, []);
    g_agents = 0;
    g_apos = "";
    if (llGetListLength(agents) > 0
        && llGetListEntryType(agents, 0) != TYPE_STRING)
    {
        g_agents = llGetListLength(agents);
        // Reduce keys to anonymous positions IMMEDIATELY, capped;
        // the key list itself is freed right after this loop and no
        // key or name is ever put on the wire (coarse-tier law).
        integer i;
        integer n = g_agents;
        if (n > MAX_CENSUS_POSITIONS) n = MAX_CENSUS_POSITIONS;
        string pos_acc = "";
        for (i = 0; i < n; ++i)
        {
            // OBJECT_POS via llGetObjectDetails (doc 07 §2.5).
            list od = llGetObjectDetails(llList2Key(agents, i),
                                         [OBJECT_POS]);
            if (llGetListLength(od) >= 1)
            {
                if (pos_acc != "") pos_acc += ";";
                pos_acc += vec_field(llList2Vector(od, 0));
            }
            od = [];
        }
        g_apos = pos_acc;
    }
    agents = []; // the expensive key list never outlives this function
}

send_digest(integer sensor_count, string sensor_pos)
{
    string payload =
        FORMAT_TAG +
        "|v=1" +
        "|ts=" + (string)llGetUnixTime() +
        "|region=" + llEscapeURL(g_region) +
        "|fps=" + (string)g_fps +
        "|dil=" + (string)g_dil +
        "|agents=" + (string)g_agents +
        "|apos=" + g_apos +
        "|self_pos=" + vec_field(g_self_pos) +
        "|self_flags=" + (string)g_self_flags +
        "|env_day_len=" + (string)g_env_day_len +
        "|env_day_off=" + (string)g_env_day_off +
        "|env_sec=" + (string)g_env_sec +
        "|parcel=" + llEscapeURL(g_parcel) +
        "|parcel_area=" + (string)g_parcel_area +
        "|parcel_flags=" + (string)g_parcel_flags +
        "|parcel_id=" + g_parcel_id +
        "|sensor_count=" + (string)sensor_count +
        "|sensor_pos=" + sensor_pos +
        "|free_mem=" + (string)llGetFreeMemory();
    if (CONFIG_SECRET != "")
    {
        payload += "|auth=" + CONFIG_SECRET;
    }

    // Fallback behaviour (doc 07 §5.5 ladder): no endpoint configured
    // or HTTP throttled -> keep sensing, say so ONCE to the owner,
    // drop this digest, try again next cycle. Email is deliberately
    // NOT a fallback: doc 07 §2.3 — a worn attachment must not rely
    // on it (20 s sleep, SVC-23 silent receive loss).
    if (CONFIG_ENDPOINT_URL == "")
    {
        if (!g_announced_unconfigured)
        {
            g_announced_unconfigured = TRUE;
            llOwnerSay("Heimdall HUD: no endpoint configured; " +
                       "sensing locally, relay idle. " + BUILD_VERSION);
        }
        return;
    }
    g_http_id = llHTTPRequest(CONFIG_ENDPOINT_URL,
        [HTTP_METHOD, "POST",
         HTTP_MIMETYPE, "text/plain;charset=utf-8"],
        payload);
    if (g_http_id == NULL_KEY)
    {
        // Throttled (doc 07 §3.2): do NOT retry immediately — that
        // extends the throttle. Next 60 s tick is the retry.
        g_http_id = NULL_KEY;
        llOwnerSay("Heimdall HUD: HTTP throttled; digest dropped, " +
                   "will retry next cycle.");
    }
}

default
{
    state_entry()
    {
        set_disclosure();
        llSetTimerEvent(DIGEST_INTERVAL_S);
        llOwnerSay("Heimdall HUD active. " + BUILD_VERSION +
                   " — NOT yet tested in-world (Phase 1 blocked).");
    }

    attach(key id)
    {
        if (id != NULL_KEY)
        {
            // Re-assert the transparency anchor on every wear.
            set_disclosure();
            llSetTimerEvent(DIGEST_INTERVAL_S);
        }
        else
        {
            llSetTimerEvent(0.0);
        }
    }

    on_rez(integer start_param)
    {
        // URLs/state do not survive rez (doc 07 §3.3); this build
        // holds no HTTP-in URL, so re-registration is just: disclosure
        // back up, timer running, owner told we are alive again
        // (the companion's Astrid-Law moment — no silent shut-out).
        set_disclosure();
        llSetTimerEvent(DIGEST_INTERVAL_S);
        llOwnerSay("Heimdall HUD rezzed; sensing resumed.");
    }

    changed(integer change)
    {
        if (change & (CHANGED_REGION | CHANGED_TELEPORT | CHANGED_REGION_START))
        {
            set_disclosure();
            llOwnerSay("Heimdall HUD: region change noted; " +
                       "next digest carries the new place.");
        }
    }

    touch_start(integer total_number)
    {
        // Touch-info disclosure line (doc 05): anyone who touches the
        // HUD hears, at whisper range, that the wearer is a scripted
        // agent; the owner gets it privately as well.
        llWhisper(0, DISCLOSURE_TEXT);
        llOwnerSay(DISCLOSURE_TEXT);
    }

    timer()
    {
        gather_sync_fields();
        // llSensor is asynchronous: the digest is completed and sent
        // from sensor / no_sensor below. One-shot sweep (not
        // llSensorRepeat) so the cadence stays the timer's 60 s.
        // Types AGENT|ACTIVE|PASSIVE = agents + objects (doc 07 §2.5:
        // SCRIPTED is NOT inclusive, so it is not OR-ed in here).
        // Attachments cannot detect their own wearer — the wearer's
        // own position travels as self_pos instead.
        llSensor("", NULL_KEY, AGENT | ACTIVE | PASSIVE,
                 SENSOR_RANGE_M, PI);
    }

    sensor(integer num_detected)
    {
        // LSL caps sensor results at the 16 nearest (doc 07 §2.5).
        string pos_acc = "";
        integer i;
        for (i = 0; i < num_detected; ++i)
        {
            if (pos_acc != "") pos_acc += ";";
            pos_acc += vec_field(llDetectedPos(i));
        }
        send_digest(num_detected, pos_acc);
    }

    no_sensor()
    {
        send_digest(0, "");
    }

    http_response(key request_id, integer status, list metadata,
                  string body)
    {
        if (request_id != g_http_id) return; // stale response; ignore
        g_http_id = NULL_KEY;
        if (status < 200 || status >= 300)
        {
            // Surfaced to the owner, never retried in a hot loop —
            // repeated 5xx trips the error throttle (doc 07 §3.2).
            llOwnerSay("Heimdall HUD: endpoint answered HTTP " +
                       (string)status + "; digest not confirmed.");
        }
        // On 2xx the body is ignored by design: the companion takes
        // no instructions from the response in this slice (no RLV
        // relay, no remote control — doc 07 §5.6 invariants).
    }
}
