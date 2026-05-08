/**
 * Build a CSV from a header row + array of data rows and trigger a browser
 * download. Each cell is RFC-4180-quoted: wrapped in double quotes, with
 * embedded double quotes doubled.
 *
 * Usage:
 *   downloadCsv('section_data.csv',
 *               ['Post Title', 'Section', 'Start Line'],
 *               items.map(i => [i.post_title || '', i.section_name || '', i.start_line || 0]));
 *
 * Cells are stringified via String(val) — callers should default null/undefined
 * to '' (or 0 for numeric columns) themselves so the output doesn't contain
 * the literal strings "null" or "undefined".
 */
window.downloadCsv = function(filename, headers, rows) {
    const escape = val => '"' + String(val).replace(/"/g, '""') + '"';
    const csv = [headers, ...rows]
        .map(row => row.map(escape).join(','))
        .join('\n') + '\n';

    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    URL.revokeObjectURL(url);
    document.body.removeChild(a);
};
