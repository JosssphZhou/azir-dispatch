// The filter: `/` and a few letters, and the office shows only the desks you
// meant. Twenty agents is a floor plan you have to page through; "waiting" or
// "sso" is one screen.
//
// Two decisions worth writing down.
//
// It filters, it does not search. There is no result list, no cursor jumping to a
// match: the floor simply has fewer people on it, and everything else (paging, the
// compact list, the desk hitboxes, the counts in the header) keeps working because
// it is all downstream of the same array. A filter that reached into the layout
// would have needed every one of those to learn about it.
//
// Every term must match, and matching is plain substring. No regex, no globs, no
// negation. A filter box that quietly means something clever is a filter box you
// have to test hypotheses against, and this one is for when you are in a hurry.
import { sanitize } from './text.mjs';
import { status as statusOf } from './theme.mjs';

// Enough for a couple of words, which is all a filter ever is. The cap exists so
// the header chip has a bounded thing to draw rather than to protect anything.
export const MAX_FILTER = 40;

// The words people actually reach for, mapped to the statuses herdr reports. They
// are aliases rather than the only way in: `blocked` still works, because that is
// what the API calls it and somebody will type it.
const WORDS = {
  待批准: 'blocked', 等待: 'blocked', 工作中: 'working', 空闲: 'idle', 已完成: 'done', 状态未知: 'unknown',
  needs: 'blocked', unsure: 'unknown',
  waiting: 'blocked',
  stuck: 'blocked',
  blocked: 'blocked',
  hand: 'blocked',
  working: 'working',
  busy: 'working',
  idle: 'idle',
  quiet: 'idle',
  done: 'done',
  finished: 'done',
  unknown: 'unknown',
};

// The words for a status, gathered the other way round: the aliases above plus the
// label actually printed on the nameplate. These go into the haystack as ordinary
// text, which is what makes half a word work. Typing `wai` has to narrow toward the
// raised hands rather than emptying the floor, and typing what you can plainly read
// on a desk ("needs you", "unsure") has to find that desk. The exact-word rule below
// still runs first, so `done` remains a status and not a substring hunt.
const ALIASES = {};
for (const [word, name] of Object.entries(WORDS)) (ALIASES[name] = ALIASES[name] || []).push(word);
const statusWords = (name) => `${statusOf(name).label} ${(ALIASES[name] || []).join(' ')}`;

// Everything about a person that a term is allowed to match. Deliberately not the
// pane's `cwd` in full: a term is matched against the last path segment as well as
// the whole thing, so `office` finds a desk in ~/projects/herdr-office without
// making every desk under ~/projects match `projects`... which it also does, since
// the full path is in here too. Both, because "find the desks in that repo" and
// "find the desks under that tree" are both things people mean.
function haystack(person) {
  const cwd = String(person?.cwd || '');
  const leaf = cwd.split('/').filter(Boolean).pop() || '';
  return [
    person?.name,
    person?.kind,
    person?.status,
    statusWords(person?.status),
    person?.tabName,
    person?.workspaceName,
    person?.title,
    person?.command,
    person?.event?.label,
    leaf,
    cwd,
  ]
    .map((v) => String(v ?? '').toLowerCase())
    .filter(Boolean)
    .join('\0');
}

export function terms(query) {
  return sanitize(String(query ?? ''))
    .toLowerCase()
    .split(/\s+/)
    .filter(Boolean);
}

export function matches(person, query) {
  const words = terms(query);
  if (!words.length) return true;
  const hay = haystack(person);
  return words.every((word) => {
    // A status word means the status, and nothing else: typing `done` to find the
    // finished desks must not also drag in somebody whose tab is called
    // "done-migration". The alias table is small and the intent is unambiguous.
    const status = WORDS[word];
    if (status) return person?.status === status;
    return hay.includes(word);
  });
}

export function filterPeople(people = [], query = '') {
  if (!terms(query).length) return people;
  return people.filter((person) => matches(person, query));
}

// Text going into the field. Same rules as the assign field (an escape sequence is
// not text, backspace deletes, ctrl-u clears), and enter means "keep this filter
// and give the keyboard back".
export function typeFilterChunk(text, chunk) {
  const current = String(text ?? '');
  const str = String(chunk ?? '');
  if (!str || str.startsWith('\x1b')) return { text: current, done: false };
  if (str === '\x7f' || str === '\b') return { text: current.slice(0, -1), done: false };
  if (str === '\x15') return { text: '', done: false };
  if (str === '\x17') return { text: current.replace(/\s*\S+\s*$/, ''), done: false };
  const stop = Math.min(...['\r', '\n'].map((c) => (str.includes(c) ? str.indexOf(c) : Infinity)));
  const head = stop === Infinity ? str : str.slice(0, stop);
  const clean = sanitize(head).replace(/\s+/g, ' ');
  const room = Math.max(0, MAX_FILTER - current.length);
  return { text: current + clean.slice(0, room), done: stop !== Infinity };
}
