// vim: tabstop=2 shiftwidth=2 expandtab

// Call a get API command and return an array containing the time taken for
// the response to be received and the reponse values in a map
// or throw an error if one is raised
var bbc_api_get = function(host, path) {
  var url = host + path
  var req_time = new Date().getTime();
  console.debug(url);
  return fetch(url)
  .then(function(response) {
    if (!response.ok) {
      throw new Error('Url "' + url + '" returned ' + response.statusText);
    }
    return response.text()
  })
  .then(function(text) {
    var sec_elapsed = (new Date().getTime() - req_time) / 1000
    return [sec_elapsed, JSON.parse(text)]
  })
  .catch(function(err) {
    console.error('Fetch Error url("' + url + '"):', err)
    throw err;
  })
}

// Call a set API command with the set parameters in the dict parameter to
// be sent encoded as HTTP GET parameters and return an array containing the time
// taken for the response to be received and the reponse values in a map
// or throw an error if one is raised
var bbc_api_set = function(host, path, dict) {
  var url = host + path + '?' + new URLSearchParams(dict).toString();
  console.info(url);
  return fetch(url)
  .then(function(response) {
    if (!response.ok) {
      throw new Error('Url "' + url + '" returned ' + response.statusText);
    }
    return response.text();
  })
  .then(function(text) {
    return JSON.parse(text);
  })
  .catch(function(err) {
    console.error('Fetch Error: ', err)
    throw err;
  })
}

// Style the pressed button to make it look selected
// only for a short period of time (plus and minus buttons)
function press_button() {
  document.activeElement.classList.add("active");
  setTimeout(() => {
    document.activeElement.classList.remove("active");
    document.activeElement.blur();
  }, 200)
}

// A Brick Battery Controller. It stores the configuration of its remote entities
// and has functions to map actions and values with the HTML UI elements
class BBC {
  constructor(host) {
    // Path to communicate with the BBC API
    this.host = host;

    // Map containing the configuration and latest values
    this.status = {solar: {}};

    // Map of configuration changes to be set out
    this.controls = {};

    // Use this to mark this.controls as dirty i.e. some values need to be set
    // on the controller. When this is done, a poll timer also needs to be reset
    // to give the user more time to press additional buttons
    // before the command is sent
    this.command_to_send = false;
  }

  // Mark the OFF button as selected
  display_off(elem, bw) {
    setTimeout(() => document.activeElement.blur(), 200);
    var offon = bw ? ['dark', 'dark'] : ['danger', 'success'];
    elem.getElementsByClassName('on')[0].classList.remove('active', 'btn-' + offon[1]);
    elem.getElementsByClassName('on')[0].classList.add('btn-outline-' + offon[1]);
    elem.getElementsByClassName('off')[0].classList.remove('btn-outline-' + offon[0]);
    elem.getElementsByClassName('off')[0].classList.add('active', 'btn-' + offon[0]);
  }

  // Mark the ON button as selected
  display_on(elem, bw) {
    setTimeout(() => document.activeElement.blur(), 200);
    var offon = bw ? ['dark', 'dark'] : ['danger', 'success'];
    elem.getElementsByClassName('off')[0].classList.remove('active', 'btn-' + offon[0]);
    elem.getElementsByClassName('off')[0].classList.add('btn-outline-' + offon[0]);
    elem.getElementsByClassName('on')[0].classList.remove('btn-outline-' + offon[1]);
    elem.getElementsByClassName('on')[0].classList.add('active', 'btn-' + offon[1]);
  }

  // Called when the opertion ON/OFF button is pressed
  // operation "off" is like a dry run mode: the BBC will still try to
  // change A/C settings according to PV every x seconds, but will just stop
  // short of sending the actual set API command the A/C units, it is useful
  // for testing/debugging BBC settings and behaviour.
  set_operation(mode) {
    if (mode == 0) {
      this.display_off(document.getElementById('operation'));
    } else if (mode == 1) {
      this.display_on(document.getElementById('operation'));
    } else {
      return;
    }
    this.controls.operation = mode;
    this.command_to_send = true;
    app.reset_timer();
  }

  // Called when the set maximum humidity minus button is pressed
  max_htemp_minus() {
    press_button();
    if (this.controls.max_htemp > 18)
    {
      this.controls.max_htemp--;
      this.command_to_send = true;
      app.reset_timer();
    }
  }

  // Called when the set maximum humidity minus button is pressed
  max_htemp_plus() {
    press_button();
    if (this.controls.max_htemp < 30)
    {
      this.controls.max_htemp++;
      this.command_to_send = true;
      app.reset_timer();
    }
  }

  // Called when temperature or humidity text value is selected for an edit
  edit_focus() {
    app.stop_timer();
  }

  // Called when temperature or humidity text value is not edited anymore
  // typically this is done by tapping/clicking out of the text field
  edit_blur() {
    app.resume_timer();
  }

  // Called when a text value has been manually set, it would typically
  // happen when the text field loses focus (blur)
  manual_edit() {
    this.command_to_send = true;
    app.reset_timer();
  }

  // Called when the control humidity 0/1 button is pressed
  set_control_humidity(mode) {
    if (mode == 0) {
      this.display_off(document.getElementById('control-humidity'), true);
    } else if (mode == 1) {
      this.display_on(document.getElementById('control-humidity'), true);
    } else {
      return;
    }
    this.controls.control_humidity = mode;
    this.command_to_send = true;
    app.reset_timer();
  }

  // Called when the set humidity minus button is pressed for
  // for the given bbc control: 'max_shum' or 'ac_sleep_shum'
  shum_minus(property) {
    press_button();
    var values = ['0', '40', '45', '50', '100'];
    var index = values.indexOf(this.controls[property]);
    var value;
    if (index > 0) {
      value = values[index - 1];
    } else {
      value = '0';
    }
    this.controls[property] = value;
    this.command_to_send = true;
    app.reset_timer();
  }

  // Called when the set humidity minus button is pressed for
  // for the given bbc control: 'max_shum' or 'ac_sleep_shum'
  shum_plus(property) {
    this.controls.control_humidity = true;
    press_button();
    var values = ['0', '40', '45', '50', '100'];
    var index = values.indexOf(this.controls[property]);
    if (index == values.length - 1) {
      return;
    } else if (index >= 0) {
      this.controls[property] = values[index + 1];
    } else {
      this.controls[property] = values[1];
    }
    this.command_to_send = true;
    app.reset_timer();
  }

  // Called when the A/C sleep mode ON/OFF is pressed
  sleep_power(mode) {
    if (mode == 0) {
      this.display_off(document.getElementById('sleep-power'));
    } else if (mode == 1) {
      this.display_on(document.getElementById('sleep-power'));
    } else {
      return;
    }
    this.controls.ac_sleep_pow = mode;
    this.command_to_send = true;
    app.reset_timer();
  }

  // Called when the sleep mode set temperature minus button is pressed
  sleep_stemp_minus() {
    press_button();
    var mode = Number(this.controls.ac_sleep_mode);
    var stemp = Number(this.controls.ac_sleep_stemp);
    if ((mode == 4 && stemp > 10) ||
        ((mode == 0 || mode == 1 || mode == 3 || mode == 7) && stemp > 18))
    {
      this.controls.ac_sleep_stemp = stemp - 1;
      this.command_to_send = true;
      app.reset_timer();
    }
  }

  // Called when the sleep mode set temperature plus button is pressed
  sleep_stemp_plus() {
    press_button();
    var mode = Number(this.controls.ac_sleep_mode);
    var stemp = Number(this.controls.ac_sleep_stemp);
    if ((mode == 4 && stemp < 30) ||
        ((mode == 0 || mode == 1 || mode == 3 || mode == 7) && stemp < 32))
    {
      this.controls.ac_sleep_stemp = stemp + 1;
      this.command_to_send = true;
      app.reset_timer();
    }
  }

  // Called when any of the sleep mode A/C mode button is pressed
  sleep_set_mode(mode) {
    this.controls.ac_sleep_mode = mode;
    this.sleep_display_mode();
    this.command_to_send = true;
    app.reset_timer();
  }

  // Change the selected button for the aircon mode
  sleep_display_mode() {
    setTimeout(() => document.activeElement.blur(), 200);
    for (var x of document.getElementById('mode').getElementsByTagName('button')) {
      x.classList.remove('btn-dark', 'active');
      x.classList.add('btn-outline-dark');
    }
    var cl;
    switch (this.controls.ac_sleep_mode) {
      // Auto mode is weird. We get told we're on auto by mode 0
      // but we need to set mode 1 or 7 to request auto, yey!
      // let's not talk about the humidify mode, we cannot set it using the api and
      // it's hard to come out of it because in it, last heating temperature is set to --
      case '':  cl = document.getElementById('humidify').classList; break;
      case '0': cl = document.getElementById('auto').classList; break;
      case '1': cl = document.getElementById('auto').classList; break;
      case '2': cl = document.getElementById('dry').classList; break;
      case '3': cl = document.getElementById('cool').classList; break;
      case '4': cl = document.getElementById('heat').classList; break;
      case '6': cl = document.getElementById('fanonly').classList; break;
      case '7': cl = document.getElementById('auto').classList; break;
    }
    if (cl) {
      cl.remove('btn-outline-dark');
      cl.add('btn-dark', 'active');
    }
  }

}

// Each of the sliders is modelled as a Vue component to manage its interaction
// They are actual awesome Ion Range Sliders which sadly depend on jQuery that has
// to be imported just to make them work, at least for version 2.3
var slider = Vue.component('ion-range-slider', {
  template: '<input/>',
  props: {
    min: Number,
    max: Number,
    type: {
      type: String,
      default: "double"
    },
    step: {
      type: Number,
      default: 100
    },
    grid_num: {
      type: Number,
      default: 5
    },
    postfix: {
      type: String,
      default: " W"
    },
    changed: Function,
  },
  mounted: function() {
    var vue_comp = this;
    // Call the IRS(jQuery) scaffolding when the Vue UI is ready
    $(this.$el).ionRangeSlider({
      skin: "big",
      type: this.type,
      min: this.min,
      max: this.max,
      drag_interval: true,
      step: this.step,
      from: 0,
      to: 0,
      grid: true,
      grid_num: this.grid_num,
      postfix: this.postfix,
      onChange: function(data) {
        // Glue to bind IRS(jQuery) to Vue on UI interaction
        vue_comp.changed(
          data.from,
          data.to
        );
      }
    });
  },
  methods: {
    // Glue to bind Vue to jQuery on data change
    set: function(from, to) {
      $(this.$el).data("ionRangeSlider").update({
        from: from,
        to: to
      });
    }
  }
});

// This is the Vue.js application that handles BBC state changes and polling logic
var app = new Vue({
  el: '#app',
  data: {
    // This is the poll timer that gets reset, paused or restarted during UI interactions
    timer: null,

    // The BBC object holding all state values
    bbc: new BBC('/bbc'),

    // The most recent values, data source for the graph below
    recent_values: null,

    // The Dygraph object to plot recent values of generation and consumption
    graph: null,
  },

  // Computed are cached Vue formatting getters that don't take any parameter
  computed: {
    // Labels below are shown on the arrows of the power flow diagram

    generation_label: function() {
      var generation = this.bbc.status.solar.pv_generation || 0;
      return generation != 0 ? Math.round(generation) + "\u00A0W" : "";
    },

    grid_label: function() {
      var grid = this.bbc.status.solar.grid_import || 0;
      return grid != 0 ? Math.abs(Math.round(grid)) + "\u00A0W" : "";
    },

    ac_label: function() {
      var ac = this.bbc.status.ac_consumption || 0;
      return ac != 0 ? Math.round(ac) + "\u00A0W" : "";
    },

    outside_temp_label: function() {
      if (typeof this.bbc.status.aircons === "undefined") {
        return "";
      }
      // Calculate an average of the aircons we can communicate with
      var temps = this.bbc.status.aircons.reduce((array, x) => {
        var otemp = Number(x.otemp);
        if (!Number.isNaN(otemp)) {
          array.push(otemp);
        }
        return array;
      }, []);
      if (temps.length == 0) {
        return "(-ºC)";
      }
      var temp = temps.reduce((acc, y) => acc + y, 0) / temps.length;
      return "(" + Math.round(temp) + "ºC)";
    },

    // Message at the top
    last_set_label: function() {
      var ls = this.bbc.status.last_set
      if (typeof this.bbc.status.last_set === 'undefined') {
        return "";
      }
      var d = new Date(ls);
      if (isNaN(d)) {
        return "";
      }
      var n = new Date();
      var label = "\u00A0/ Last\u00A0set:\u00A0";
      var ago = "\u00A0ago";
      var secs = (n - d) / 1000;
      if (secs < 120) {
        return label + Math.round(secs) + "\u00A0s" + ago;
      }
      var mins = secs / 60;
      if (mins < 120) {
        return label + Math.round(mins) + "\u00A0m" + ago;
      }
      var hours = mins / 60;
      return label + Math.round(hours) + "\u00A0h" + ago;
    },
  }, // End of computed

  // Created is called when the Vue object is initialised but the UI not mounted yet
  created: function() {
    this.load_data();
    this.reset_timer();
    document.onfullscreenchange = function() {
      if (document.fullscreenElement) {
        document.getElementById('fullscreen').classList.add('hidden');
        document.getElementById('fullscreen-exit').classList.remove('hidden');
      } else {
        document.getElementById('fullscreen').classList.remove('hidden');
        document.getElementById('fullscreen-exit').classList.add('hidden');
      }
    }
  },

  methods: {

    // Return number formatted as rounded integer followed by
    // non breaking space and kWh unit
    format_energy: function(kwh_value) {
      if (isNaN(kwh_value)) {
        return "";
      }
      return Math.round(kwh_value) + "\u00A0kWh";
    },

    // Return date time using the format "Www dd/mm HH:MM:ss"
    // e.g. "Sat 03/10 08:30:15"
    format_date: function(date_string) {
      if (typeof date_string === 'undefined') {
        return "";
      }
      var d = new Date(date_string);
      if (isNaN(d)) {
        return date_string;
      }
      return ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'][d.getDay()] + ' ' +
        d.getDate() + '/' +
        (d.getMonth() + 1).toString().padStart(2, '0') + ' ' +
        d.getHours().toString().padStart(2, '0') + ':' +
        d.getMinutes().toString().padStart(2, '0') + ':' +
        d.getSeconds().toString().padStart(2, '0');
    },

    // Configure the auto-refreshing, interactive graph of recent values
    init_graph: function() {
      var vm = this;
      // Dygraph wants a JS Date object in the first array element of each value
      for (val of vm.recent_values['values']) {
        val[0] = new Date(val[0]);
      }

      let graph_labels = [];
      let headers = vm.recent_values['headers'];
      graph_labels[0] = headers[0];
      graph_labels[headers.findIndex(x => x=='pv_generation')] = 'PV\u00A0Generation';
      graph_labels[headers.findIndex(x => x=='grid_import')] = 'Grid\u00A0Import';
      graph_labels[headers.findIndex(x => x=='ac_consumption')] = 'A/C\u00A0Consumption';

      let legendFormatter = function(data) {
        if (data.x == null) {
          // This happens when there's no selection and {legend: 'always'} is set:
          // just show a legend without any values
          return data.series.map(series => '<div>' + series.dashHTML + '&nbsp;' + series.labelHTML + '</div>').join(' ');
        }
        let html = data.xHTML.split(' ')[1];
        data.series.forEach(function(series) {
          // Build a similar legend but show for each series, the value under the selection
          html += ' <div>' + series.dashHTML + '&nbsp;' + series.labelHTML + ': ' +
                  (typeof(series.yHTML) === 'undefined' ? '-' : series.yHTML) + ' W' + '</div>';
        });
        return html;
      }

      vm.graph = new Dygraph(document.getElementById("graph"),
                             vm.recent_values['values'],
                             {
                               labels: graph_labels,
                               interactionModel: {},
                               colors: ['rgb(64,128,0)', 'rgb(23,108,218)', 'rgb(252,216,105)'],
                               legend: 'always',
                               labelsDiv: document.getElementById('graph-legend'),
                               legendFormatter: legendFormatter,
                               strokeWidth: 3,
                               axes: {
                                 y: {axisLabelWidth: 35},
                               }
                             });
    },

    // Handle all the complex asynchronous polling logic
    load_data: function() {
      var vm = this;
      var cl = document.getElementById('dot').classList;
      cl.remove('pulse-green', 'pulse-red');

      // Promise that queries the brick-battery service status
      // and stores the retrieves values in our Vue instance
      var p_status = bbc_api_get(vm.bbc.host, '/status')
      .then(function([secs, map])
      {
        console.debug(map);
        vm.bbc.status = map;

        // Add latest values to the graph
        if (vm.recent_values) {
          let values = vm.recent_values['values'];
          let last_point = values[values.length - 1];

          if (last_point && new Date(vm.bbc.status.last_updated) - last_point[0] > 10 * 1000) {
            // Recent_values are more than 10s old, clear the values and let them reload next time
            vm.recent_values = undefined;
          } else {
            let headers = vm.recent_values['headers'];
            if (values.length >= 600) {
              values.shift();
            }
            let val = [];
            val[0] = new Date(map.last_updated);
            val[headers.findIndex(x => x=='pv_generation')] = map.solar.pv_generation;
            val[headers.findIndex(x => x=='grid_import')] = map.solar.grid_import;
            val[headers.findIndex(x => x=='ac_consumption')] = map.ac_consumption;
            values.push(val);
            vm.graph.updateOptions({'file': values});
          }
        }
        return secs;
      });

      // Promise that queries the brick-battery recent graph values and once
      // retrieved, initialises the graph with them. This is only done once
      // when the app loads up, most recent values get appended individually
      // afterwise.
      var p_recent_values;
      if (vm.recent_values) {
        p_recent_values = Promise.resolve({});
      } else {
        p_recent_values = bbc_api_get(vm.bbc.host, '/recent-values')
        .then(function([secs, values]) {
          vm.recent_values = values;
          vm.init_graph();
          return secs;
        });
      }

      // Promise that sends potentially changed controls to the brick-controller
      var p;
      if (vm.bbc.command_to_send) {
        vm.bbc.command_to_send = false;
        p = bbc_api_set(vm.bbc.host, '/controls', vm.bbc.controls);
      } else {
        p = Promise.resolve({});
      }

      // Promise that sets then queries brick-controller control values and stores them
      // on the Vue instance
      var p_controls = p
      .then(function(x) {
        return bbc_api_get(vm.bbc.host, '/controls')
      })
      .then(function([secs, map]) {
        if (vm.bbc.command_to_send) {
          // Ignore the response at this stage if we have already pressed a button
          // to avoid bumping the display while the user is still interacting with the UI
          // After the UI set timeout has expired, a new control command will be sent.
          return secs;
        }
        vm.bbc.controls = map;
        // Reflect control changes to the UI
        if (vm.bbc.controls.operation === false) {
          vm.bbc.display_off(document.getElementById('operation'));
        } else {
          vm.bbc.display_on(document.getElementById('operation'));
        }
        if (vm.bbc.controls.ac_sleep_pow === '0') {
          vm.bbc.display_off(document.getElementById('sleep-power'));
        } else {
          vm.bbc.display_on(document.getElementById('sleep-power'));
        }
        if (vm.bbc.controls.control_humidity === false) {
          vm.bbc.display_off(document.getElementById('control-humidity'), true);
        } else {
          vm.bbc.display_on(document.getElementById('control-humidity'), true);
        }
        // Set the A/C operation mode when entering sleep mode
        vm.bbc.sleep_display_mode();
        vm.$refs.loadSlider.set(vm.bbc.controls.min_load, vm.bbc.controls.max_load);
        vm.$refs.sleepSlider.set(vm.bbc.controls.sleep_threshold, vm.bbc.controls.wakeup_threshold);
        vm.$refs.setIntervalSlider.set(vm.bbc.controls.set_interval);
        return secs;
      })

      // Resolve all the above promises now
      Promise.all([p_status, p_controls, p_recent_values])
      .then(function(secs_status, secs_controls, secs_recent_values) {
        if (Math.max(secs_status, secs_controls, secs_recent_values) > 10) {
          // One of the queries took too long. It can happen when the Web App
          // is waking up from long inactivity
          return;
        }
        // Here we have all endpoints working correctly in a timely manner
        var cl = document.getElementById('dot').classList;
        cl.add('pulse-green');
      })
      .catch(function(err) {
        // Something went wrong: endpoint not responding, not reachable on the network, etc.
        var cl = document.getElementById('dot').classList;
        console.log(err);
        cl.add('pulse-red');
      });
    },

    // Stop the poll timer when the user enters a numeric value (can take time)
    stop_timer: function() {
      clearInterval(this.timer);
    },

    // Resume the poll timer when the user exits the numeric field (blur event)
    resume_timer: function() {
      this.timer = setInterval(() => {
        this.load_data();
      }, 3000);
    },

    // Reset the poll timer after each control button press
    reset_timer: function() {
      clearInterval(this.timer);
      this.timer = setInterval(() => {
        this.load_data();
      }, 3000);
    },

    // Called when the Import control range slider values change,
    // triggered by a user interaction
    load_slider_changed: function(from, to) {
      this.bbc.controls.min_load = from;
      this.bbc.controls.max_load = to;
      this.bbc.command_to_send = true;
      this.reset_timer();
    },

    // Called when the Wakeup / sleep thresholds slider values change,
    // triggered by a user interaction
    sleep_slider_changed: function(from, to) {
      this.bbc.controls.sleep_threshold = from;
      this.bbc.controls.wakeup_threshold = to;
      this.bbc.command_to_send = true;
      this.reset_timer();
    },

    // Called when the Set interval slider values change,
    // triggered by a user interaction
    set_interval_slider_changed: function(from) {
      this.bbc.controls.set_interval = from;
      this.bbc.command_to_send = true;
      this.reset_timer();
    },

    // Called when the user presses tne fullscreen top left square icon
    enter_fullscreen: function() {
      document.documentElement.requestFullscreen();
    },

    // Called when the user exits the fullscreen moder, using the inverted
    // square icon or any other browser event that triggers it
    exit_fullscreen: function() {
      document.exitFullscreen();
    }
  }
}) // The end of the Vue instance declaration
