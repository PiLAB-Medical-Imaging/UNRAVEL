# -*- coding: utf-8 -*-
"""
Created on Wed Aug 16 11:25:30 2023

@author: DELINTE Nicolas
"""


import numpy as np
from sklearn.cluster import KMeans
from dipy.io.stateful_tractogram import Space, StatefulTractogram, Origin
from dipy.io.streamline import load_tractogram, save_tractogram
from unravel.utils import tract_to_ROI, xyz_to_spherical
from skimage.morphology import flood
from sklearn.neighbors import KernelDensity


def extract_nodes(trk_file: str, level: int = 3, smooth: bool = True):
    '''
    The start is assumed to be the lowest position along the last axis.

    Parameters
    ----------
    trk_file : str
        Path to tractogram file
    level : int, optional
        Number of steps in the mean streamline trajectory. The number of steps
        is equal to (2**level)+1. The default is 3.

    Returns
    -------
    point_array : 2D array of size (n, 3)
        Coordinates (x,y,z) of the n mean trajectory points.

    '''

    trk = load_tractogram(trk_file, 'same')
    trk.to_vox()
    trk.to_corner()

    streams = trk.streamlines
    streams_data = trk.streamlines.get_data()

    # Clustering end nodes based on streamline directions
    end_0 = streams_data[streams._offsets, :]
    end_1 = np.roll(streams_data[streams._offsets-1, :], -1, axis=0)
    dirs = end_1-end_0
    kmeans = KMeans(n_clusters=2, n_init="auto").fit(dirs)

    # Assigning start and end based on clustering
    start = end_0.copy()
    end = end_1.copy()
    start[kmeans.labels_ == 1, :] = end_1[kmeans.labels_ == 1, :]
    end[kmeans.labels_ == 1, :] = end_0[kmeans.labels_ == 1, :]

    # Only compute the mean end points of long fibers [Q3:Q3+1.5*IQR]
    q1, q3 = np.percentile(streams._lengths, [25, 75])
    long_streamlines = streams._lengths > q3
    outlier_streamlines = streams._lengths > q3+1.5*(q3-q1)
    selec_streamlines = long_streamlines*~outlier_streamlines
    m_start = np.mean(start[selec_streamlines], axis=0)
    m_end = np.mean(end[selec_streamlines], axis=0)

    # Re-orders start and end based on main axial direction,
    # first main direction or three-way vote
    # !!! does not always work
    diff_abs = np.abs(m_start-m_end)
    main_dir = np.argmax(diff_abs)
    small_dir = np.argmin(diff_abs)
    vote = np.sum(np.where(m_start-m_end > 0, 1, -1))
    if diff_abs[main_dir] > (np.sum(diff_abs)-diff_abs[main_dir]):
        if m_start[main_dir] > m_end[main_dir]:
            m_start, m_end = m_end, m_start
    elif diff_abs[small_dir] < (np.sum(diff_abs)-diff_abs[small_dir])/4:
        idx = 1 if small_dir == 0 else 0
        if m_start[idx] > m_end[idx]:
            m_start, m_end = m_end, m_start
    elif vote > 0:
        m_start, m_end = m_end, m_start

    # Iterating over specified level ---------------------------------

    # point_array = np.vstack((m_start, m_end))

    point_array = np.zeros((2**level+1, 3))
    point_array[0] = m_start
    point_array[-1] = m_end
    normal_array = np.zeros(point_array.shape)
    normal_array[0] = m_start-m_end
    normal_array[-1] = m_start-m_end

    for j in range(level):
        for i in range(2**j):

            m_start = point_array[2**(level-j-1)*2*i]
            m_end = point_array[2**(level-j-1)*(2*i+2)]

            # Computing normal of perpendicular surface at midpoint
            midpoint = (m_start+m_end)/2
            normal = m_start-m_end
            normal_array[2**(level-j-1)*(2*i+1)] = normal

            # Computing normal at start
            normal_previous = normal_array[2**(level-j-1)*(2*i)]
            ns_previous = streams_data-m_start
            sign_previous = np.where(np.sum(ns_previous*normal_previous,
                                            axis=1) > 0, 1, -1)

            # Computing normal at end
            normal_next = normal_array[2**(level-j-1)*(2*i+2)]
            ns_next = streams_data-m_end
            sign_next = np.where(np.sum(ns_next*normal_next, axis=1) > 0, 1, -1)

            # Creating filter based on previous and next surface
            mp_previous = midpoint-m_start
            mp_next = midpoint-m_end
            mp_sign_previous = np.where(np.sum(mp_previous*normal_previous) > 0,
                                        1, -1)
            mp_sign_next = np.where(np.sum(mp_next*normal_next) > 0, 1, -1)
            sign_previous = np.where(sign_previous == mp_sign_previous, 1, 0)
            sign_next = np.where(sign_next == mp_sign_next, 1, 0)
            idx_filter = np.argwhere(sign_next+sign_previous != 2)

            # Find indexes that cross the surface
            ns = streams_data-midpoint
            sign = np.where(np.sum(ns*normal, axis=1) > 0, 1, 0)
            idx = np.argwhere(abs(np.roll(sign, 1)-sign) == 1)
            idx = np.array(
                list(filter(lambda x: x not in streams._offsets, idx)))
            idx = np.array(list(filter(lambda x: x not in idx_filter, idx)))

            # Computing mean position on the surface
            try:
                points = streams_data[idx, :]
                point_array[2**(level-j-1)*(2*i+1)] = np.mean(points, axis=0)
            except IndexError:
                point_array[2**(level-j-1)*(2*i+1)] = midpoint

    if smooth:
        _, point_array = get_dist_from_median_trajectory(trk_file, point_array,
                                                         compute_dist=False)

    return point_array


def get_streamline_number_from_index(streams, index: int) -> int:
    '''

    Parameters
    ----------
    streams : streamlines.array_sequence.ArraySequence
        DESCRIPTION.
    index : int or array (n,1)
        Number of the tractography point (x,y,z).

    Returns
    -------
    nb : int or array(n,)
        Streamline number.

    '''

    offsets = np.append(streams._offsets, len(streams._data))
    isin = np.where(offsets-index > 0, 1, 0)
    if type(index) == int:
        nb = np.argwhere(np.roll(isin, -1)-isin == 1)[0][0]
    else:
        nb = np.argwhere(np.roll(isin, -1, axis=1)-isin == 1)[:, 1]

    return nb


def remove_streamlines(streams, idx: int):
    '''


    Parameters
    ----------
    streams : streamlines.array_sequence.ArraySequence
        DESCRIPTION.
    idx : int
        Streamline number.

    Yields
    ------
    sl : streamline generator
        DESCRIPTION.

    '''

    for i, sl in enumerate(streams):
        if i not in idx:
            yield sl


def get_dist_from_median_trajectory(trk_file: str, point_array,
                                    compute_dist: bool = True):
    '''


    Parameters
    ----------
    trk_file : str
        Path to tractogram file.
    point_array : 2D array of size (n, 3)
        Coordinates (x,y,z) of the n mean trajectory points.
    compute_dist : bool, optional
        Set to false to only compute median, speeds up the code.
        The default is True.

    Returns
    -------
    dist : TYPE
        DESCRIPTION.
    median_array : TYPE
        DESCRIPTION.

    '''

    trk = load_tractogram(trk_file, 'same')
    trk.to_vox()
    trk.to_corner()

    streams = trk.streamlines
    streams_data = trk.streamlines.get_data()

    # Center of mass
    trk_roi = tract_to_ROI(trk_file)
    center = tuple([np.average(indices) for indices in np.where(trk_roi == 1)])

    dist = np.zeros((point_array.shape[0], len(streams._offsets)))
    median_array = point_array.copy()

    for i, point in enumerate(point_array):

        if i == 0:
            continue
        if i == point_array.shape[0]-1:
            break

        # Computing normal of perpendicular surface at midpoint
        midpoint = point_array[i]
        normal = point_array[i-1]-point_array[i+1]

        # Find indexes that cross the surface
        ns = streams_data-midpoint
        sign = np.where(np.sum(ns*normal, axis=1) > 0, 1, 0)
        idx = np.argwhere(abs(np.roll(sign, 1)-sign) == 1)
        idx = np.array(list(filter(lambda x: x not in streams._offsets, idx)))

        # Must be same side as midpoint to center of mass
        n_mp_com = midpoint-center
        n_xyz_com = streams_data-center
        com_filter = np.argwhere(np.sum(n_mp_com*n_xyz_com, axis=1) < 0)
        idx = np.array(list(filter(lambda x: x not in com_filter, idx)))

        # Find position
        idx_pos = np.take_along_axis(streams_data, idx, axis=0)
        median = np.median(idx_pos, axis=0)
        median_array[i] = median

        if not compute_dist:
            continue

        # Find distance
        idx_dist = np.linalg.norm(idx_pos-np.repeat(median[np.newaxis, :],
                                                    idx_pos.shape[0], axis=0),
                                  axis=1)

        for j, i_dist in enumerate(idx_dist):

            n = get_streamline_number_from_index(streams, idx[j])
            dist[i, n] = i_dist

    return dist, median_array


def remove_outlier_streamlines(trk_file, point_array, out_file: str = None,
                               outlier_ratio: float = .5,
                               remove_outlier_dir: bool = False,
                               verbose: bool = True, bandwidth: float = 0.2,
                               neighbors_required: int = 5,
                               bandwidth_dir: float = 1,
                               neighbors_required_dir: int = 10,
                               keep_ratio: float = 0.5):
    '''
    Removes streamlines that are outliers for more than half (default) of the
    bundle trajectory based on the distance to the mean trajectory. Can also
    remove streamlines if their main direction is an outlier with the
    remove_outlier_dir parameter.

    Parameters
    ----------
    trk_file : str
        Path to tractogram file.
    point_array : 2D array of size (n, 3)
        Coordinates (x,y,z) of the n mean trajectory points.
    out_file : str, optional
        Path to output file. The default is None.
    outlier_ratio : int, optional
        Percentage of the streamline allowed to be an outlier [0:1]. Increasing
        the value removes less streamlines. The default is 0.5 (50%).
    remove_outlier_dir : bool, optional
        If True, removes streamlines whose direction are outliers.
        The default is False.
    verbose : bool, optional
        If True, prints number of streamlines removed. The default is False.
    bandwidth : float, optional.
        Bandwidth for the KDE, recommended values : [0.1-5]. The default is 0.2.
    neighbors_required : int, optional
        Approximative number of neighboring points required to not be removed.
        The default is 5.
    bandwidth_dir : float, optional.
        Bandwidth for the KDE, recommended values : [0.1-5]. The default is 1.
    neighbors_required_dir : int, optional
        Approximative number of neighboring points required to not be removed.
        The default is 10.
    keep_ratio : float, optional
        Maximum percentage of streamlines that can be removed.
        The default is 0.5.

    Returns
    -------
    None.

    '''

    trk = load_tractogram(trk_file, 'same')
    trk.to_vox()
    trk.to_corner()

    streams = trk.streamlines

    bandwidth = bandwidth*neighbors_required

    streams_data = trk.streamlines.get_data()
    dens = np.zeros((point_array.shape[0], len(streams._offsets)))

    for i, point in enumerate(point_array):

        if i == 0:
            continue
        if i == point_array.shape[0]-1:
            break

        # Computing normal of perpendicular surface at midpoint
        midpoint = point_array[i]
        normal = point_array[i-1]-point_array[i+1]
        normal = normal/np.linalg.norm(normal)

        # Find indexes that cross the surface
        ns = streams_data-midpoint
        dot = np.sum(ns*normal, axis=1)
        sign = np.where(dot > 0, 1, 0)
        idx = np.argwhere(abs(np.roll(sign, 1)-sign) == 1)
        idx = np.array(list(filter(lambda x: x not in streams._offsets, idx)))

        # Find position
        idx_pos = np.take_along_axis(streams_data, idx, axis=0)

        # Project onto plane
        ns_pos = idx_pos-midpoint
        dot_pos = dot[idx[:, 0]]
        proj_onto_plane = (ns_pos - dot_pos[..., np.newaxis]*normal) + midpoint

        z_vec = np.array([0, 0, 1])
        y_comp = z_vec - (z_vec@normal)*normal
        y_comp = y_comp/np.linalg.norm(y_comp)
        x_comp = np.cross(y_comp, normal)

        proj_mat = -np.vstack([x_comp, y_comp])  # build projection matrix
        points_2D = proj_onto_plane @ proj_mat.T       # apply projection

        kde_model = KernelDensity(
            kernel='gaussian', bandwidth=bandwidth).fit(points_2D)
        kde = np.exp(kde_model.score_samples(points_2D))*len(points_2D)

        n = get_streamline_number_from_index(streams, idx)

        # Saves densities in decreasing order to keep worse density of
        # streamlines crossing plane multiple times
        kde_decrease_idx = (-kde).argsort()
        dens[i, n[kde_decrease_idx]] = kde[kde_decrease_idx]

    # Compute outliers
    t = neighbors_required/(2*np.pi*bandwidth**2)
    m = np.mean(dens, axis=1, where=dens != 0)
    m = np.repeat(m[..., np.newaxis], dens.shape[1], axis=1)
    outliers = dens <= t
    outliers[dens == 0] = False
    outliers[dens > m] = False
    outliers = outliers[1:-1, :]

    # Remove if more than outlier_ratio of pathway is outlier
    n_sign = np.sum(outliers, axis=0)
    n_val = np.sum(dens > 0, axis=0)
    n_idx = np.argwhere(n_sign > n_val*outlier_ratio)

    if len(n_idx) > keep_ratio*len(n_sign):

        sorted_indexes = np.argsort(-n_sign)
        keep_num_idx = int(len(n_sign)*keep_ratio)
        n_idx = sorted_indexes[:keep_num_idx]

    if remove_outlier_dir:

        streams_data = trk.streamlines.get_data()

        # Clustering end nodes based on streamline directions
        end_0 = streams_data[streams._offsets, :]
        end_1 = np.roll(streams_data[streams._offsets-1, :], -1, axis=0)
        dirs = end_1-end_0
        kmeans = KMeans(n_clusters=2, n_init="auto").fit(dirs)

        # Assigning start and end based on clustering
        start = end_0.copy()
        end = end_1.copy()
        start[kmeans.labels_ == 1, :] = end_1[kmeans.labels_ == 1, :]
        end[kmeans.labels_ == 1, :] = end_0[kmeans.labels_ == 1, :]

        # Only compute the mean end points of long fibers [Q3:Q3+1.5*IQR]
        q1, q3 = np.percentile(streams._lengths, [25, 75])
        long_streamlines = streams._lengths > q3
        outlier_streamlines = streams._lengths > q3+1.5*(q3-q1)
        selec_streamlines = long_streamlines*~outlier_streamlines
        m_start = np.mean(start[selec_streamlines], axis=0)
        m_end = np.mean(end[selec_streamlines], axis=0)

        average_dir = m_end-m_start

        # Send to spherical coordinates in degrees centered on average dir
        r, theta, phi = xyz_to_spherical(end-start)
        r_a, theta_a, phi_a = xyz_to_spherical(average_dir[np.newaxis, ...])
        X = np.stack((theta-theta_a, phi-phi_a), axis=1)
        X = np.where(X < -np.pi, X+2*np.pi, X)
        X = X*180/np.pi

        bw = bandwidth_dir
        nb = neighbors_required_dir
        bw = bw*nb
        kde_model = KernelDensity(kernel='gaussian', bandwidth=bw).fit(X)
        dens = np.exp(kde_model.score_samples(X))*len(X)

        thresh = nb/(2*np.pi*bw**2)

        n_idx_gaus = np.argwhere(dens < thresh)

        if verbose:
            print(str(n_idx_gaus.shape[0]) +
                  ' streamlines removed based on direction')
        n_idx = np.concatenate((n_idx, n_idx_gaus))

    streams = remove_streamlines(streams, n_idx)

    if out_file is None:
        out_file = trk_file

    if verbose:
        print(str(len(n_idx))+' streamlines removed from tract')
    trk_new = StatefulTractogram(streams, trk, Space.VOX,
                                 origin=Origin.TRACKVIS)
    save_tractogram(trk_new, out_file)


def get_roi_sections_from_nodes(trk_file: str, point_array,
                                simplify_shape: bool = True):
    '''
    Create a mask containing the subdivisions of a tract along its mean
    trajectory.

    Parameters
    ----------
    trk_file : str
        Path to tractogram file.
    point_array : 2D array of size (n, 3)
        Coordinates (x,y,z) of the n mean trajectory points.
    simplify_shape : bool, optional
        Removes spurious treamlines and increases robustness but increases
        computation time. Not necessary on straight fiber bundles. Default=True.

    Returns
    -------
    mask: 3D array of size (x,y,z)
        Labeled array containing the volumes of the section of the tract.

    '''

    trk_roi = tract_to_ROI(trk_file)
    mask = np.zeros(trk_roi.shape)

    # Center of mass
    center = tuple([np.average(indices) for indices in np.where(trk_roi == 1)])

    # Meshgrid of coordinates
    x_val = np.linspace(0.5, trk_roi.shape[0]-0.5, trk_roi.shape[0])
    y_val = np.linspace(0.5, trk_roi.shape[1]-0.5, trk_roi.shape[1])
    z_val = np.linspace(0.5, trk_roi.shape[2]-0.5, trk_roi.shape[2])
    xyz = np.stack(np.meshgrid(x_val, y_val, z_val, indexing='ij'), axis=3)
    xyz_flat = xyz.reshape(xyz.shape[0]*xyz.shape[1]*xyz.shape[2], 3)

    for i, _ in enumerate(point_array):

        if i == 0:
            continue

        try:
            m_previous = point_array[i-2]
        except IndexError:
            m_previous = point_array[i-1]
        m_start = point_array[i-1]
        m_end = point_array[i]
        try:
            m_next = point_array[i+1]
        except IndexError:
            m_next = point_array[i]
        midpoint = (m_start+m_end)/2

        # Computing normals
        n_start = m_previous-m_end
        n_end = m_start-m_next
        n_mp_start = m_start-midpoint
        n_mp_end = midpoint-m_end
        n_xyz_start = m_start-xyz_flat
        n_xyz_end = xyz_flat-m_end

        # Find indexes that are between current plane and previous plane
        sign_xyz_start = np.where(
            np.sum(n_start*n_xyz_start, axis=1) > 0, 1, -1)
        sign_xyz_end = np.where(np.sum(n_end*n_xyz_end, axis=1) > 0, 1, -1)

        # Must be same side as midpoint
        sign_mp_start = np.where(np.sum(n_start*n_mp_start) > 0, 1, -1)
        sign_mp_end = np.where(np.sum(n_end*n_mp_end) > 0, 1, -1)

        sign_mp_slice = (np.where(sign_xyz_start == sign_mp_start, 1, 0)
                         + np.where(sign_xyz_end == sign_mp_end, 1, 0))
        sign_mp_slice = np.where(sign_mp_slice == 2, 1, 0)

        # Must be same side as midpoint to center of mass
        n_mp_com = midpoint-center
        n_xyz_com = xyz_flat-center
        sign_mp_com = np.where(np.sum(n_mp_com*n_xyz_com, axis=1) > 0, 1, 0)

        roi_mp_slice = sign_mp_slice.reshape(trk_roi.shape)*trk_roi
        roi_mp_com = sign_mp_com.reshape(trk_roi.shape)*trk_roi
        roi = roi_mp_slice + roi_mp_com

        # Adding only regions selected by filters but connected to midpoint
        if simplify_shape:
            dot = tuple(np.floor(midpoint).astype(int))
            if roi[dot] == 2:
                roi_connec = flood(roi*roi_mp_slice, dot, tolerance=1,
                                   connectivity=1)
                roi = np.where(roi_connec == 1, 2, 0)

        mask[roi == 2] = i

    return mask.astype(int)


def smooth_streamlines(trk_file: str, out_file: str = None):
    '''
    Slightly smooth streamlines. The step size will no longer be uniform after
    smoothing.

    Parameters
    ----------
    trk_file : str
        Path to tractogram file.
    out_file : str, optional
        Path to output file. The default is None.

    Returns
    -------
    None.

    '''

    trk = load_tractogram(trk_file, 'same')
    trk.to_vox()
    trk.to_corner()

    streams = trk.streamlines
    point = streams.get_data()

    smoothed_point = (np.roll(point, 1, axis=0) + point +
                      np.roll(point, -1, axis=0))/3

    streams._data = smoothed_point

    # Setting end points back to original values
    starts = streams._offsets
    ends = starts-1
    streams._data[starts] = point[starts]
    streams._data[ends] = point[ends]

    if out_file is None:
        out_file = trk_file

    trk_new = StatefulTractogram(streams, trk, Space.VOX,
                                 origin=Origin.TRACKVIS)
    save_tractogram(trk_new, out_file)
