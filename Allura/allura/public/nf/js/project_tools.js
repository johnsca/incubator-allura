/*
       Licensed to the Apache Software Foundation (ASF) under one
       or more contributor license agreements.  See the NOTICE file
       distributed with this work for additional information
       regarding copyright ownership.  The ASF licenses this file
       to you under the Apache License, Version 2.0 (the
       "License"); you may not use this file except in compliance
       with the License.  You may obtain a copy of the License at

         http://www.apache.org/licenses/LICENSE-2.0

       Unless required by applicable law or agreed to in writing,
       software distributed under the License is distributed on an
       "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
       KIND, either express or implied.  See the License for the
       specific language governing permissions and limitations
       under the License.
*/

(function() {
    // Provide CSRF protection
    var cval = $.cookie('_session_id');
    var csrf_input = $('<input name="_session_id" type="hidden" value="'+cval+'">');
    // Install popup
    var install_popup = $('#lightbox_install_modal');
    var install_form = $('#install_form');
    var new_ep_name = install_form.find('input.new_ep_name');
    var new_mount_point = install_form.find('input.new_mount_point');
    var new_mount_label = install_form.find('input.new_mount_label');
    var install_tool_label = $('#install_tool_label');
    var mount_point_rule_names = $('#install_form .mount-point-rule-names');
    install_popup.append(install_form.show());
    $('a.install_trig').click(function () {
        var datatool = $(this).data('tool');
        var relaxed_mount_points = $(this).data('relaxed-mount-points');
        install_form.find('.mount-point-name-rules').hide();
        if (datatool) {
            var tool = defaults[datatool];
            install_tool_label.html(tool.default_label);
            new_ep_name.val(datatool);
            new_mount_point.val(tool.default_mount);
            new_mount_label.val(tool.default_label);
            if (relaxed_mount_points) {
              install_form.find('.mount-point-name-rules.tool-relaxed').show();
            }
            else {
              install_form.find('.mount-point-name-rules.tool').show();
            }
        } else {
            install_tool_label.html("Subproject");
            new_ep_name.val('');
            new_mount_point.val('');
            new_mount_label.val('');
            install_form.find('.mount-point-name-rules.subproject').show();
        }
    });
    // Edit popup
    var $popup_title = $('#popup_title');
    var $popup_contents = $('#popup_contents');
    $('a.admin_modal').click(function () {
        var link = this;
        $popup_title.html('');
        $popup_contents.html('Loading...');
        $.get(link.href, function (data) {
            $popup_title.html($(link).html());
            $popup_contents.html(data);
            $popup_contents.find('form').append(csrf_input);
        });
    });
    // delete popup
    var form_to_delete = null;
    var mount_delete_popup = $('#lightbox_mount_delete');
    var mount_delete_form = $('#mount_delete_form');
    mount_delete_popup.append(mount_delete_form.show());
    mount_delete_form.find('.continue_delete').click(function () {
        form_to_delete.submit();
        form_to_delete = null;
    });
    mount_delete_form.find('.cancel_delete').click(function () {
        form_to_delete = null;
    });
    $('a.mount_delete').click(function () {
        var tool_label = 'this';
        var mount_point = $(this).data('mount-point');
        if (mount_point) {
            tool_label = 'the "' + mount_point + '"';
        }
        $('div.warning_msg').text('Warning: This will destroy all data in ' + tool_label + ' tool and is non-reversable!');

        form_to_delete = this.parentNode;
        return false;
    });
    // sorting
    $('#sortable').sortable({items: ".fleft:not(.isnt_sorted)"}).bind( "sortupdate", function (e) {
        var sortables = $('#sortable .fleft');
        var tools = 0;
        var subs = 0;
        var params = {'_session_id':$.cookie('_session_id')};
        for (var i = 0, len = sortables.length; i < len; i++) {
            var item = $(sortables[i]);
            var mount_point = item.find('input.mount_point');
            var shortname = item.find('input.shortname');
            if (mount_point.length) {
                params['tools-' + tools + '.mount_point'] = mount_point.val();
                params['tools-' + tools + '.ordinal'] = i;
                tools++;
            }
            if (shortname.length) {
                params['subs-' + subs + '.shortname'] = shortname.val();
                params['subs-' + subs + '.ordinal'] = i;
                subs++;
            }
        }
        $.ajax({
            type: 'POST',
            url: 'update_mount_order',
            data: params,
            success: function(xhr, textStatus, errorThrown) {
                $('#messages').notify('Tool order updated, refresh this page to see the updated project navigation.',
                                      {status: 'confirm'});
            },
            error: function(xhr, textStatus, errorThrown) {
                $('#messages').notify('Error saving tool order.',
                                      {status: 'error'});
            }
        });
    });
    // fix firefox scroll offset bug
    var userAgent = navigator.userAgent.toLowerCase();
    if(userAgent.match(/firefox/)) {
      $('#sortable').bind( "sortstart", function (event, ui) {
        ui.helper.css('margin-top', $(window).scrollTop() );
      });
      $('#sortable').bind( "sortbeforestop", function (event, ui) {
        ui.helper.css('margin-top', 0 );
      });
    }
})();
