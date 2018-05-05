/*	Extract links from a page
 */

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

let x = document.body.querySelectorAll('a[href]');
let ret = [];
for (let i=0; i < x.length; i++) {
	if (isClickable (x[i])) {
		ret.push (x[i].href);
	}
}
ret; /* immediately return results, for use with Runtime.evaluate() */
