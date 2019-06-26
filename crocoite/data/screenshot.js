/* Find and scrollable full-screen elements and return their actual size
 */
(function () {
/* limit the number of elements queried */
let elem = document.querySelectorAll ('body > div');
let ret = [];
for (let i = 0; i < elem.length; i++) {
	let e = elem[i];
	let s = window.getComputedStyle (e);
	if (s.getPropertyValue ('position') == 'fixed' &&
			s.getPropertyValue ('overflow') == 'auto' &&
			s.getPropertyValue ('left') == '0px' &&
			s.getPropertyValue ('right') == '0px' &&
			s.getPropertyValue ('top') == '0px' &&
			s.getPropertyValue ('bottom') == '0px') {
		ret.push (e.scrollHeight);
	}
}
return ret; /* immediately return results, for use with Runtime.evaluate() */
})();
