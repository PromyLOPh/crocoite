/*	Extract links from a page
 */

(function () {
/* --- copy&paste from click.js --- */
/*	Element is visible if itself and all of its parents are
 */
function isVisible (o) {
	if (o === null || !(o instanceof Element)) {
		return true;
	}
	let style = window.getComputedStyle (o);
	if ('parentNode' in o) {
		return style.display !== 'none' && isVisible (o.parentNode);
	} else {
		return style.display !== 'none';
	}
}

/*	Elements are considered clickable if they are a) visible and b) not
 *	disabled
 */
function isClickable (o) {
	return !o.hasAttribute ('disabled') && isVisible (o);
}
/* --- end copy&paste */

let ret = [];
['a[href]', 'area[href]'].forEach (function (s) {
	let x = document.body.querySelectorAll(s);
	for (let i=0; i < x.length; i++) {
		if (isClickable (x[i])) {
			ret.push (x[i].href);
		}
	}
});

/* If Chrome loads plain-text documents it’ll wrap them into <pre>. Check those
 * for links as well, assuming the whole line is a link (i.e. list of links). */
let x = document.querySelectorAll ('body > pre');
for (let i=0; i < x.length; i++) {
	if (isVisible (x[i])) {
		x[i].innerText.split ('\n').forEach (function (s) {
			if (s.match ('^https?://')) {
				ret.push (s);
			}
		});
	}
}
return ret; /* immediately return results, for use with Runtime.evaluate() */
})();
