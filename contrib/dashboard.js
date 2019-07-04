/* configuration */
let socket = "wss://localhost:6789/",
	urllogMax = 100;

function formatSize (bytes) {
	let prefixes = ['B', 'KiB', 'MiB', 'GiB', 'TiB'];
	while (bytes >= 1024 && prefixes.length > 1) {
		bytes /= 1024;
		prefixes.shift ();
	}
	return bytes.toFixed (1) + ' ' + prefixes[0];
}

class Job {
	constructor (id, url, user, queued) {
		this.id = id;
		this.url = url;
		this.user = user;
		this.status = undefined;
		this.stats = {'pending': 0, 'have': 0, 'running': 0,
				'requests': 0, 'finished': 0, 'failed': 0,
				'bytesRcv': 0, 'crashed': 0, 'ignored': 0};
		this.urllog = [];
		this.queued = queued;
		this.started = undefined;
		this.finished = undefined;
		this.aborted = undefined;
	}

	addUrl (url) {
		if (this.urllog.push (url) > urllogMax) {
			this.urllog.shift ();
		}
	}
}

let jobs = {};
let ws = new WebSocket(socket);
ws.onmessage = function (event) {
	var msg = JSON.parse (event.data);
	let msgdate = new Date (Date.parse (msg.date));
	var j = undefined;
	if (msg.job) {
		j = jobs[msg.job];
		if (j === undefined) {
			j = new Job (msg.job, 'unknown', '<unknown>', new Date ());
			Vue.set (jobs, msg.job, j);
		}
	}
	if (msg.uuid == '36cc34a6-061b-4cc5-84a9-4ab6552c8d75') {
		j = new Job (msg.job, msg.url, msg.user, msgdate);
		/* jobs[msg.job] = j does not work with vue, see
		https://vuejs.org/v2/guide/list.html#Object-Change-Detection-Caveats
		*/
		Vue.set (jobs, msg.job, j);
		j.status = 'pending';
	} else if (msg.uuid == '46e62d60-f498-4ab0-90e1-d08a073b10fb') {
		j.status = 'running';
		j.started = msgdate;
	} else if (msg.uuid == '7b40ffbb-faab-4224-90ed-cd4febd8f7ec') {
		j.status = 'finished';
		j.finished = msgdate;
	} else if (msg.uuid == '865b3b3e-a54a-4a56-a545-f38a37bac295') {
		j.status = 'aborted';
		j.aborted = msgdate;
	} else if (msg.uuid == '5c0f9a11-dcd8-4182-a60f-54f4d3ab3687') {
		/* forwarded job message */
		let rmsg = msg.data;
		if (rmsg.uuid == '24d92d16-770e-4088-b769-4020e127a7ff') {
			/* job status */
			Object.assign (j.stats, rmsg);
		} else if (rmsg.uuid == '5b8498e4-868d-413c-a67e-004516b8452c') {
			/* recursion status */
			Object.assign (j.stats, rmsg);
		} else if (rmsg.uuid == 'd1288fbe-8bae-42c8-af8c-f2fa8b41794f') {
			/* fetch */
			j.addUrl (rmsg.url);
		}
	}
};
ws.onopen = function (event) {
};
ws.onerror = function (event) {
};

Vue.component('job-item', {
  props: ['job', 'jobs'],
  template: '<div class="job box" :id="job.id"><ul class="columns"><li class="jid column is-narrow"><a :href="\'#\' + job.id">{{ job.id }}</a></li><li class="url column"><a :href="job.url">{{ job.url }}</a></li><li class="status column is-narrow"><job-status v-bind:job="job"></job-status></li></ul><job-stats v-bind:job="job"></job-stats></div>',
});
Vue.component('job-status', {
	props: ['job'],
	template: '<span v-if="job.status == \'pending\'">queued on {{ job.queued.toLocaleString() }}</span><span v-else-if="job.status == \'aborted\'">aborted on {{ job.aborted.toLocaleString() }}</span><span v-else-if="job.status == \'running\'">running since {{ job.started.toLocaleString() }}</span><span v-else-if="job.status == \'finished\'">finished since {{ job.finished.toLocaleString() }}</span>'
});
Vue.component('job-stats', {
  props: ['job'],
  template: '<div><progress class="progress is-info" :value="job.stats.have" :max="job.stats.have+job.stats.pending+job.stats.running"></progress><ul class="stats columns"><li class="column">{{ job.stats.have }} <small>have</small></li><li class="column">{{ job.stats.running }} <small>running</small></li><li class="column">{{ job.stats.pending }} <small>pending</small></li><li class="column">{{ job.stats.requests }} <small>requests</small><li class="column"><filesize v-bind:value="job.stats.bytesRcv"></filesize></li></ul><job-urls v-bind:job="job"></job-urls></div>'
});
Vue.component('job-urls', {
  props: ['job'],
  template: '<ul class="urls"><li v-for="u in job.urllog">{{ u }}</li></ul>'
});
Vue.component('filesize', {
  props: ['value'],
  template: '<span class="filesize">{{ fvalue }}</span>',
  computed: { fvalue: function () { return formatSize (this.value); } }
});
Vue.component('bot-status', {
	props: ['jobs'],
	template: '<nav class="level"><div class="level-item has-text-centered"><div><p class="heading">Pending</p><p class="title">{{ stats.pending }}</p></div></div><div class="level-item has-text-centered"><div><p class="heading">Running</p><p class="title">{{ stats.running }}</p></div></div><div class="level-item has-text-centered"><div><p class="heading">Finished</p><p class="title">{{ stats.finished+stats.aborted }}</p></div></div><div class="level-item has-text-centered"><div><p class="heading">Transferred</p><p class="title"><filesize v-bind:value="stats.totalBytes"></filesize></p></div></div></nav>',
	computed: {
		stats: function () {
			let s = {pending: 0, running: 0, finished: 0, aborted: 0, totalBytes: 0};
			for (let k in this.jobs) {
				let j = this.jobs[k];
				s[j.status]++;
				s.totalBytes += j.stats.bytesRcv;
			}
			return s;
		}
	}
});

let app = new Vue({
  el: '#app',
  data: {
	jobs: jobs,
  }
});

