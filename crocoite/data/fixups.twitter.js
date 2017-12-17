/* Fixups for twitter: Some accounts are hidden behind a “suspicious activity”
 * message, click that.
 */
(function(){
function fixup () {
	var show = document.querySelector ("button.ProfileWarningTimeline-button"); 
	if (show) {
		var click = new MouseEvent('click', {
				view: window,
				bubbles: true,
				cancelable: true
				});
		show.dispatchEvent (click);
	}
}
window.addEventListener("load", fixup);
}());
