/* Fixups for twitter:
 * - Some accounts are hidden behind a “suspicious activity” message, click
 *   that.
 * - Click “more replies” buttons periodically (as they popup when scrolling)
 */
(function(){
function makeClickEvent () {
	return new MouseEvent('click', {
				view: window,
				bubbles: true,
				cancelable: true
				});
}
function expandThread () {
	let links = document.querySelectorAll('a.ThreadedConversation-moreRepliesLink');
	for (let i = 0; i < links.length; i++) {
		links[i].dispatchEvent (makeClickEvent ());
	}
	return true;
}
function showProfile () {
	var show = document.querySelector ("button.ProfileWarningTimeline-button");
	if (show) {
		show.dispatchEvent (makeClickEvent ());
	}
}
window.addEventListener("load", showProfile);
/* XXX: can we use a mutation observer instead? */
window.setInterval (expandThread, 1000);
}());
